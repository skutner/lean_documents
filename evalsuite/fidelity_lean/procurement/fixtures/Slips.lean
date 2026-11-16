-- Each fixture changes one engine flag. Faithful.lean supplies the candidate's
-- own engine; no fixture depends on evaluator-only Reference definitions.
namespace Candidate
open Procurement

def counts_requester_signature : Rule := fun w => assess w "requester"
def ignores_finance_requirement : Rule := fun w => assess w "finance"
def ignores_delegation : Rule := fun w => assess w "delegation"
def applies_delegation_outside_dates : Rule := fun w => assess w "dates"
def ignores_amendment : Rule := fun w => assess w "amendment"
def applies_amendment_too_early : Rule := fun w => assess w "early"
def treats_limit_as_exclusive : Rule := fun w => assess w "exclusive"
def uses_superseded_policy : Rule := fun w => assess w "previous"
def uses_wrong_month_rate : Rule := fun w => assess w "month"
def ignores_same_week_rule : Rule := fun w => assess w "week"
def ignores_conversion : Rule := fun w => assess w "conversion"
def counts_nonapproving_role : Rule := fun w => assess w "approving"

end Candidate
