import Std
#eval do
  let marker ← IO.FS.Handle.mk "/proc/self/fd/2" .write
  marker.putStrLn "SLEEP_REACHED"
  marker.flush
  -- A sleeping loop is harmless even without containment; finite 10 minute cap.
  for _ in [:600] do IO.sleep 1000
