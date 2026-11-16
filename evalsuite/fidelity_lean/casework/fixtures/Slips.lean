-- Interface.lean and Faithful.lean precede this file. Each entry switches one
-- generator rule or input, using the exact RULE_OF and input-slip identifiers.
namespace Candidate
open Casework

def withSlip (slip : String) : Rule := evaluateVariant slip

def wrong_class_period : Rule := withSlip "wrong_class_period"
def ignores_amendment : Rule := withSlip "ignores_amendment"
def applies_amendment_too_early : Rule := withSlip "applies_amendment_too_early"
def counts_issue_day : Rule := withSlip "counts_issue_day"
def ignores_nonworking_shift : Rule := withSlip "ignores_nonworking_shift"
def ignores_holiday : Rule := withSlip "ignores_holiday"
def ignores_shift_after_extension : Rule := withSlip "ignores_shift_after_extension"
def counts_refused_extension : Rule := withSlip "counts_refused_extension"
def counts_pending_extension : Rule := withSlip "counts_pending_extension"
def counts_void_extension : Rule := withSlip "counts_void_extension"
def extends_urgent_notice : Rule := withSlip "extends_urgent_notice"
def ignores_extension : Rule := withSlip "ignores_extension"
def ignores_postal_rule : Rule := withSlip "ignores_postal_rule"
def counts_calendar_days_for_post : Rule := withSlip "counts_calendar_days_for_post"
def late_on_deadline_day : Rule := withSlip "late_on_deadline_day"
def counts_deadline_day_in_days_late : Rule := withSlip "counts_deadline_day_in_days_late"
def uses_draft_register : Rule := withSlip "uses_draft_register"
def takes_neighbouring_row : Rule := withSlip "takes_neighbouring_row"
end Candidate
