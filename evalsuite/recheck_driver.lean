/- Trusted sequential driver. PLAN is replaced only by validated supervisor constants.
Child bytes are JSON strings inside events, never parsed as driver events. -/
import Lean
import Std.Sync.Mutex

open Lean

def emit (value : Json) : IO Unit := do
  let out ← IO.getStdout
  out.putStrLn value.compress
  out.flush

def event (kind : String) (fields : List (String × Json) := []) : IO Unit :=
  emit (Json.mkObj (("event", toJson kind) :: fields))

-- A single lock serializes frames from the two pipe-draining tasks.
def chunk (bytes : ByteArray) (label : String) (index : Nat)
    (lock : Std.Mutex Nat) : IO Unit := do
  lock.atomically do
    let total ← get
    let next := total + bytes.size
    set next
    if next > 1048576 then
      event "overflow"
      IO.Process.exit 90
    event "output" [("index", toJson index), ("stream", toJson label),
      ("bytes", toJson (bytes.data.map UInt8.toNat))]

def drain (stream : IO.FS.Handle) (label : String) (index : Nat)
    (lock : Std.Mutex Nat) : IO Unit := do
  -- Handle.read uses fread: requesting 4096 can hide a short marker until EOF.
  -- Read buffered bytes individually; emit at newlines or a fixed 4096-byte cap.
  let mut pending := ByteArray.empty
  repeat
    let bytes ← stream.read 1
    if bytes.isEmpty then
      if !pending.isEmpty then chunk pending label index lock
      break
    pending := pending.push bytes[0]!
    if bytes[0]! == 10 || pending.size == 4096 then
      chunk pending label index lock
      pending := ByteArray.empty

def run (args : Array String) (index : Nat) (lock : Std.Mutex Nat) : IO UInt32 := do
  event "start" [("index", toJson index), ("argv", toJson args)]
  let child ← IO.Process.spawn {cmd := "/lean/bin/lean", args := args, stdin := .null, stdout := .piped, stderr := .piped}
  let a ← IO.asTask (drain child.stdout "stdout" index lock) .dedicated
  let b ← IO.asTask (drain child.stderr "stderr" index lock) .dedicated
  let code ← child.wait
  IO.ofExcept a.get
  IO.ofExcept b.get
  event "exit" [("index", toJson index), ("code", toJson code.toNat)]
  return code

-- PLAN

def main : IO UInt32 := do
  IO.FS.createDirAll "/scratch/build"
  let lock ← Std.Mutex.new 0
  let version ← IO.Process.output {cmd := "/lean/bin/lean", args := #["--version"]}
  if version.exitCode != 0 || !(version.stdout.startsWith "Lean (version 4.24.0,") then
    event "version_error" [("text", toJson version.stdout)]
    return 91
  event "version" [("text", toJson version.stdout)]
  for index in [:plan.size] do
    let (args, object) := plan[index]!
    if let some path := object then
      IO.FS.createDirAll (System.FilePath.mk path).parent.get!
    let code ← run args index lock
    if code != 0 then return 1
    if let some path := object then
      if !(← System.FilePath.pathExists path) then
        event "missing_object" [("index", toJson index)]
        return 92
      event "object" [("index", toJson index), ("path", toJson path)]
  event "complete"
  return 0
