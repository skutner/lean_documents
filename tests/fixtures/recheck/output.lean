import Std
#eval do
  let chunk := String.fromUTF8! (ByteArray.mk (Array.replicate 4096 120))
  let out ← IO.FS.Handle.mk "/proc/self/fd/1" .write
  let err ← IO.FS.Handle.mk "/proc/self/fd/2" .write
  -- Exactly 64 MiB combined at most, even if every limit is broken.
  for _ in [:8192] do
    out.putStr chunk
    err.putStr chunk
