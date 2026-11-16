import Std
#eval do
  let chunk := ByteArray.mk (Array.replicate 1048576 65)
  for i in [:96] do
    IO.FS.writeBinFile s!"/scratch/fill-{i}" chunk
