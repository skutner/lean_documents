import Std.Internal.UV.TCP
#eval do
  IO.println "TCP_OPERATION_REACHED"
  let out ← IO.getStdout
  out.flush
  let socket ← Std.Internal.UV.TCP.Socket.new
  try
    let promise ← socket.connect (.v4 ⟨Std.Net.IPv4Addr.ofParts 127 0 0 1, 39761⟩)
    match promise.result!.get with
    | .ok _ => IO.println "TCP_UNEXPECTED_CONNECTED"
    | .error err => IO.println s!"TCP_DENIED: {err}"
  catch err => IO.println s!"TCP_DENIED: {err}"
  IO.sleep 1000
