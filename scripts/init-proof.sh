#!/usr/bin/env bash
# The README's "Try it", run for real on repositories that have never seen the
# platform.
#
# usage: scripts/init-proof.sh WORKDIR
#
# The Try it block is three commands: install warden from the release tag,
# `warden init`, `warden certify --level 3`. This reads that block from the
# README through scripts/readme-try-it.sh rather than retyping it, and runs
# its second and third commands in a fresh Python, Node, Go and Java
# repository under WORKDIR, with whichever `warden` is first on PATH. CI puts the wheel built from the pull request there, so the
# install line is the one command not run as written: it must name this
# warden's version, the tag a reader would install.
#
# Each repository holds only a manifest and one source file, committed; the
# Java one also holds one JUnit 5 test, so the scope init writes for it has a
# test to run (tests/test_init_proof.py runs it). For each one:
#   - init exits 0;
#   - the paths git sees as new are exactly the ones init reports writing, so
#     nothing the proof passes on was written by hand;
#   - certify exits 0 and reports LEVEL 3 or above;
#   - with the enrollment committed, `warden declare check` exits 0 and reports
#     no DRIFT.
#
# Exit 1 names the language and the condition that failed, with the command's
# output. Exit 2 means the proof could not run. WORKDIR keeps the repositories,
# so the workflow each init wrote can be linted afterwards, and the block as
# read, try-it.txt.
set -euo pipefail

workdir="${1:?usage: scripts/init-proof.sh WORKDIR}"
readme="${INIT_PROOF_README:-$(cd "$(dirname "$0")/.." && pwd)/README.md}"
platform="https://github.com/NightWatchEng/nightgate"

fail() {
  echo "init proof: $1: $2" >&2
  if [ -n "${3:-}" ] && [ -f "$3" ]; then
    sed 's/^/  | /' "$3" >&2
  fi
  exit 1
}

g() {
  git -c user.name=init-proof -c user.email=init-proof@example.invalid \
      -c commit.gpgsign=false "$@"
}

# A documented command is split into words and run, never handed to a shell,
# so a line that needs one is refused rather than run differently.
run_line() {
  case "$1" in
    *[\;\&\|\<\>\$\`\\\"\'\(\)\*\?\[]*)
      echo "init proof: \`$1\` needs a shell to mean what it says; the Try it block must be plain commands" >&2
      return 2
      ;;
  esac
  set -f
  # shellcheck disable=SC2086
  set -- $1
  set +f
  "$@"
}

if ! command -v warden >/dev/null 2>&1; then
  echo "init proof: no warden on PATH; the proof DID NOT RUN" >&2
  exit 2
fi
[ -f "$readme" ] || { echo "init proof: no README at $readme; the proof DID NOT RUN" >&2; exit 2; }

mkdir -p "$workdir"
workdir="$(cd "$workdir" && pwd)"

# The block as scripts/readme-try-it.sh prints it, kept as a file so that it
# is counted byte for byte: a blank line inside the fence — before, between
# or after the commands — counts against the three, where a command
# substitution would have dropped a trailing one.
try_it="$workdir/try-it.txt"
bash "$(dirname "$0")/readme-try-it.sh" "$readme" > "$try_it" \
  || fail README "no closed fenced block under \"## Try it\""
count="$(awk 'END { print NR }' "$try_it")"
[ "$count" = 3 ] || fail README "the Try it block holds $count command(s), not the three it promises (install, init, certify)"

install_line="$(sed -n 1p "$try_it")"
init_line="$(sed -n 2p "$try_it")"
certify_line="$(sed -n 3p "$try_it")"

version="$(warden --version)"
version="${version#warden }"
[ "$install_line" = "uv tool install git+$platform@v$version" ] \
  || fail README "the install line is \`$install_line\`, not the v$version release this warden is"
case "$init_line" in
  "warden init" | "warden init "*) ;;
  *) fail README "the second command is \`$init_line\`, not warden init" ;;
esac
case "$certify_line" in
  "warden certify "*) ;;
  *) fail README "the third command is \`$certify_line\`, not warden certify" ;;
esac

prove() {
  local lang="$1" repo="$workdir/$1" out
  rm -rf "$repo"
  mkdir -p "$repo"
  case "$lang" in
    python)
      printf '[project]\nname = "demo"\nversion = "0.1.0"\n' > "$repo/pyproject.toml"
      printf 'def add(a, b):\n    return a + b\n' > "$repo/demo.py"
      ;;
    node)
      printf '{"name": "demo", "version": "0.1.0", "scripts": {"test": "node --test"}}\n' > "$repo/package.json"
      printf 'exports.add = (a, b) => a + b;\n' > "$repo/index.js"
      ;;
    go)
      printf 'module example.com/demo\n\ngo 1.22\n' > "$repo/go.mod"
      printf 'package demo\n\nfunc Add(a, b int) int { return a + b }\n' > "$repo/demo.go"
      ;;
    java)
      mkdir -p "$repo/src/main/java/demo" "$repo/src/test/java/demo"
      cat > "$repo/pom.xml" <<'EOF'
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>0.1.0</version>
  <properties>
    <maven.compiler.release>17</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.11.4</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.5.2</version>
      </plugin>
    </plugins>
  </build>
</project>
EOF
      printf 'package demo;\n\npublic class Demo {\n    public static int add(int a, int b) { return a + b; }\n}\n' \
        > "$repo/src/main/java/demo/Demo.java"
      printf 'package demo;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\nimport org.junit.jupiter.api.Test;\n\nclass DemoTest {\n    @Test\n    void adds() { assertEquals(3, Demo.add(1, 2)); }\n}\n' \
        > "$repo/src/test/java/demo/DemoTest.java"
      ;;
  esac
  (cd "$repo" && g init -q -b main && g add -A && g commit -qm fixture) >/dev/null

  out="$workdir/$lang-init.txt"
  (cd "$repo" && run_line "$init_line") > "$out" 2>&1 \
    || fail "$lang" "\`$init_line\` exited non-zero" "$out"

  local reported written
  reported="$(awk '$1 == "wrote" || $1 == "updated" { print $2 }' "$out" | LC_ALL=C sort)"
  written="$(cd "$repo" && g add -A && g diff --cached --name-only --no-renames | LC_ALL=C sort)"
  [ -n "$reported" ] || fail "$lang" "init reported writing no file" "$out"
  [ "$reported" = "$written" ] || fail "$lang" "the new paths are not exactly the ones init reported writing
  reported: $(printf '%s' "$reported" | tr '\n' ' ')
  new:      $(printf '%s' "$written" | tr '\n' ' ')" "$out"

  out="$workdir/$lang-certify.txt"
  (cd "$repo" && run_line "$certify_line") > "$out" 2>&1 \
    || fail "$lang" "\`$certify_line\` exited non-zero" "$out"
  grep -Eq '^certification: LEVEL [3-9] ' "$out" \
    || fail "$lang" "certify did not report LEVEL 3 or above" "$out"

  (cd "$repo" && g commit -qm "enroll in warden") >/dev/null
  out="$workdir/$lang-declare.txt"
  (cd "$repo" && warden declare check) > "$out" 2>&1 \
    || fail "$lang" "warden declare check exited non-zero" "$out"
  if grep -q DRIFT "$out"; then
    fail "$lang" "warden declare check reported DRIFT on what init wrote" "$out"
  fi
  echo "init proof: $lang: init wrote $(echo "$reported" | awk 'END { print NR }') file(s), $(grep -E '^certification: ' "$workdir/$lang-certify.txt"), declare check clean"
}

for lang in python node go java; do
  prove "$lang"
done
