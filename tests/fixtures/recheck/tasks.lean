import Std
#eval do
  IO.println "TASKS_REACHED"
  (← IO.getStdout).flush
  let mut children := #[]
  -- At most 16 children, each sleeping for only 10 seconds.
  -- This also leaves headroom below 200 OS tasks for Lean runtime threads.
  for _ in [:16] do
    try
      let child ← IO.Process.spawn {cmd := "/lean/bin/lean", args := #["--threads=1", "--run", "/checks/Sleeper.lean"], stdin := .null, stdout := .null, stderr := .null}
      children := children.push child
    catch err => IO.println s!"TASK_DENIED {err}"
  for child in children do
    let _ ← child.wait
