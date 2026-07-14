"""The ONE way an SMS or email leaves Adapix.

Every text/email path — owner-approved sends, auto-mode campaign sends, and
inbound auto-replies — goes through send_outbound() so the rules live in
exactly one place:

  1. Opted-out cont