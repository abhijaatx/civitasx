package civitas

default allow := false

allow if {
  input.action == "answer"
  input.evidence_verified == true
}

allow if {
  input.action == "name_station"
  input.station_evidence == true
}

allow if {
  input.action == "draft_private"
  input.private == true
  input.submission_requested == false
}

allow if {
  input.action == "submit"
  input.approval == true
  input.receipt_verified == true
}

allow if {
  input.action == "start_submission"
  input.approval == true
  input.connector_enabled == true
  input.resident_confirmation == true
}

allow if {
  input.action == "record_receipt"
  input.submission_started == true
  input.receipt_verified == true
  input.content_hash_match == true
}

allow if {
  input.action == "publish"
  input.approval == true
  input.redaction_reviewed == true
}

decision := {
  "allow": allow,
  "action": input.action,
  "reason": reason,
}

reason := "evidence and policy requirements satisfied" if allow
reason := "policy requirements are not satisfied" if not allow
