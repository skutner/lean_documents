-- Evaluator-only; concatenate after Interface.lean. Lean 4.24.0 core.
namespace Ledger.Reference

def live (w : World) : List Entry := w.entries.filter (fun e => !e.voided)

def lacks (w : World) (e : Entry) : Bool := match e.approval with
  | .present => false
  | .absent => true
  | .unknown => !((w.approvals.find? (fun p => p.1 == e.id)).map Prod.snd).getD false

def sum (es : List Entry) : Nat := (es.map Entry.amount).foldl (· + ·) 0

-- Strict improvement preserves Python max's first-in-list tie behavior.
def largest (es : List Entry) : Option (String × Nat) :=
  es.foldl (fun best e => match best with
    | none => some (e.id, e.amount)
    | some p => if e.amount > p.2 then some (e.id, e.amount) else best) none

def summarize (w : World) (es : List Entry) (missing : Entry → Bool) : Summary :=
  let ms := es.filter missing
  let n := sum es
  ⟨n, (n : Int) - w.budget, largest es, ms.length, sum ms,
   (es.filter (fun e => e.approval == .present)).length, ms.length + w.unsampledMissing⟩

def rules : Rule := fun w => summarize w (live w) (lacks w)

def includesVoided : Rule := fun w => summarize w w.entries (lacks w)

def supersededAmount : Rule := fun w =>
  summarize w ((live w).map (fun e => { e with amount := e.ledgerAmount })) (lacks w)

def unknownAsAbsent : Rule := fun w =>
  summarize w (live w) (fun e => e.approval != .present)

def unknownAsPresent : Rule := fun w =>
  summarize w (live w) (fun e => e.approval == .absent)

def unsampledIgnored : Rule := fun w =>
  let r := rules w
  { r with populationMissing := r.missingCount }

-- Python round(nonnegative rational): nearest integer, ties to even.
def rounded (n d : Nat) : Nat :=
  let q := n / d
  let r := n % d
  if 2 * r > d || (2 * r == d && q % 2 == 1) then q + 1 else q

def extrapolatesSampleRate : Rule := fun w =>
  let es := live w
  let projected := rounded ((es.filter (fun e => e.approval == .absent)).length * w.population) es.length
  { rules w with populationMissing := projected }

def reversedDirection : Rule := fun w =>
  let r := rules w
  { r with gap := -r.gap }

def arithmeticSlip : Rule := fun w =>
  let r := rules w
  { r with total := r.total + 1, gap := r.gap + 1 }

def missingSheetAsPresent : Rule := fun w =>
  { rules w with recordedCount := ((live w).filter (fun e => e.approval != .absent)).length }

def wrongEntry : Rule := fun w =>
  let smallest := (live w).foldl (fun best e => match best with
    | none => some (e.id, e.amount)
    | some p => if e.amount < p.2 then some (e.id, e.amount) else best) none
  { rules w with largest := smallest }

inductive ClaimSpec where
  | total (value : Int)
  | gap (value : Int) (over : Bool)
  | largest (id : String) (amount : Nat)
  | missingCount (value : Int) (exact : Bool)
  | missingAmount (value : Int) (exact : Bool)
  | recorded (value : Int)
  | population (value : Int) (exact : Bool)

def bound (n : Nat) (value : Int) (exact : Bool) : Bool :=
  if exact then (n : Int) == value else (n : Int) >= value

def test (rule : Rule) (spec : ClaimSpec) (w : World) : Bool :=
  let r := rule w
  match spec with
  | .total v => (r.total : Int) == v
  | .gap v over => (if over then r.gap else -r.gap) == v
  | .largest id a => r.largest == some (id, a)
  | .missingCount v exact => bound r.missingCount v exact
  | .missingAmount v exact => bound r.missingAmount v exact
  | .recorded v => (r.recordedCount : Int) == v
  | .population v exact => bound r.populationMissing v exact
end Ledger.Reference
