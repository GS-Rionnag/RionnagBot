# The Third Eye only

Scope: guild 1549838808680374293. This server has independent entertainment features;
Rionnag onboarding, role resets, managers, forms and scrim rules do not apply here.
Read docs/servers/third_eye/README.md. Keep commands/events in cogs, pure text rules in
text.py and lock persistence in storage.py. Never persist message bodies or webhook tokens.

Preserve /uwuify as a member-only-argument toggle requiring Manage Messages. Keep the
message-author name/avatar webhook behavior, URL-only bypass, attachment preservation,
no-ping policy, failure rollback and server guards. State belongs in
 data/servers/third_eye/locks.sqlite3 (ignored); migration is one-time and must not reapply
old lock rows after someone is unlocked. This module runs in the installed shared service;
never start the retired standalone trollbot client.
