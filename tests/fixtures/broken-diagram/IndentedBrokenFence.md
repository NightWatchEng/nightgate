# Fixture: a broken diagram the column-0 extractor could not see

This fence is indented inside a list item. GitHub renders it; an extractor
anchored at column 0 skips it entirely. It uses `graph` as a node id, which is
a reserved Mermaid keyword and a genuine parse error.

1. A step, with its diagram nested under it:

   ```mermaid
   flowchart TB
       graph["a node named graph"]
   ```

CI runs the validator against this directory and REQUIRES it to fail. If this
file ever passes, the gate has gone blind again.
