import Std
#eval do
  IO.FS.writeFile "/scratch/positive" "scratch-ok"
  IO.println (← IO.FS.readFile "/scratch/positive")
  IO.println s!"HOME={← IO.getEnv "HOME"}"
  IO.println (← IO.FS.readFile "/proc/self/status")
  IO.println (← IO.FS.readFile "/proc/self/limits")
  IO.println (← IO.FS.readFile "/proc/mounts")
  IO.sleep 1000
