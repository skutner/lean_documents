-- Own fixture: verify catalogue call order against the installed Lean core.
#check List.all
#check List.any
#check List.map
#check List.filter

def allRows (xs : List Nat) : Bool := List.all xs (fun x => x == x)
def anyRow (xs : List Nat) : Bool := List.any xs (fun x => x == x)
def mapRows (xs : List Nat) : List Nat := List.map (fun x => x) xs
def filterRows (xs : List Nat) : List Nat := List.filter (fun x => x == x) xs
example : allRows [2, 3] = true := by decide
example : anyRow [2, 3] = true := by decide
example : mapRows [2, 3] = [2, 3] := by decide
example : filterRows [2, 3] = [2, 3] := by decide
