import Std
#eval do
  let handle ← IO.FS.Handle.mk "/scratch/large" .write
  let chunk := ByteArray.mk (Array.replicate 1048576 65)
  for _ in [:40] do handle.write chunk
