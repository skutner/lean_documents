import Std
#eval do
  let _ ← IO.Process.spawn {cmd := "/lean/bin/lean", args := #["--threads=1", "--run", "/checks/Sleeper.lean"], stdin := .null, stdout := .null, stderr := .null}
  let mut ready := false
  for _ in [:200] do
    if ← System.FilePath.pathExists "/scratch/child-ready" then
      ready := true
      break
    IO.sleep 10
  if !ready then throw (IO.userError "descendant never became ready")
  IO.println "DESCENDANT_STARTED"
