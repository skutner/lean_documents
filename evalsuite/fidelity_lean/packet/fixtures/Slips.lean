-- Each candidate changes one part of the operational rule.
namespace Candidate
open Packet

def ageExclusive : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && c.testedDay + (w.maxAge : Int) > w.reviewDay)

def ignoresFailedResult : Rule := fun w u => u.certificate.any (fun c =>
  c.testedDay + (w.maxAge : Int) >= w.reviewDay)

def ignoresAgeLimit : Rule := fun _ u => u.certificate.any (fun c => c.result == .pass)

def flipsOutcome : Rule := fun w u => !(meets w u)

def wrongPeriod365 : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && c.testedDay + 365 >= w.reviewDay)

def dayCountMinusOne : Rule := fun w u => u.certificate.any (fun c =>
  c.result == .pass && c.testedDay + (w.maxAge : Int) + 1 >= w.reviewDay)
end Candidate
