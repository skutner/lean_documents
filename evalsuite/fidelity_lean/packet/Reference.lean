-- Evaluator-only: follows generate.packet's M-02 and may_stay closure.
namespace Packet.Reference

def meets : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && w.reviewDay - c.testedDay <= (w.maxAge : Int))

def ageExclusive : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && w.reviewDay - c.testedDay < (w.maxAge : Int))

def ignoresFailedResult : Rule := fun w u => u.certificate.any (fun c =>
  w.reviewDay - c.testedDay <= (w.maxAge : Int))

def ignoresAgeLimit : Rule := fun _ u => u.certificate.any (fun c => c.result == .pass)

def flipsOutcome : Rule := fun w u => !(meets w u)

def wrongPeriod365 : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && w.reviewDay - c.testedDay <= (365 : Int))

def dayCountMinusOne : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && w.reviewDay - c.testedDay - 1 <= (w.maxAge : Int))

inductive ClaimSpec where
  | service (id : String) (stays : Bool)
  | result (id : String) (wanted : Result)
  | count (value : Nat) (exact : Bool)

def test (rule : Rule) (spec : ClaimSpec) (w : World) : Bool :=
  match spec with
  | .service id stays => (w.units.find? (fun u => u.id == id)).any (fun u => rule w u == stays)
  | .result id wanted => (w.units.find? (fun u => u.id == id)).any
      (fun u => u.certificate.any (fun c => c.result == wanted))
  | .count value exact =>
    let n := (w.units.filter (rule w)).length
    if exact then n == value else n >= value
end Packet.Reference
