import Std
#eval do
  let hostPath ← IO.FS.readFile "/checks/host-path"
  for path in [hostPath, "/src/attempted"] do
    try
      IO.FS.writeFile path "changed"
      IO.println s!"WRITE_UNEXPECTED: {path}"
    catch _ => IO.println s!"WRITE_DENIED: {path}"
  IO.FS.writeFile "/scratch/positive" "SCRATCH_OK"
  IO.println (← IO.FS.readFile "/scratch/positive")
  try
    IO.println (← IO.FS.readFile hostPath)
    IO.println "READ_UNEXPECTED"
  catch _ => IO.println "SECRET_READ_DENIED"
