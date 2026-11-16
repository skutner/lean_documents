import Std
#eval do
  let marker ← IO.FS.Handle.mk "/proc/self/fd/2" .write
  marker.putStrLn "MEMORY_REACHED"
  marker.flush
  let mut retained : Array ByteArray := #[]
  -- Fixed 2304 MiB allocation cap (below 3 GiB); each initialized block is retained until completion.
  for i in [:2304] do
    retained := retained.push (ByteArray.mk (Array.replicate 1048576 (UInt8.ofNat (i % 255))))
  IO.println s!"MEMORY_COMPLETED {retained.size}"
