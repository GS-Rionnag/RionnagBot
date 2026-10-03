# Change timeline

## 2026-10-03 — restore onboarding presentation and availability controls

- Restored the MR emoji and embedded welcome/tryout panels, and username-based ticket names with a short ID suffix.
- Replaced the availability text box with a repeatable day → start/end time → more days → finish flow, with cached schedules and overnight windows.
- Added the normal-character username search note and a paginated game-account results picker; selected accounts are verified by UID.
- Updated existing tickets/panels through reconciliation, without wiping data or triggering a form-version reset.
- Added applicant mentions in message content, with explicit mention permissions for new welcome/application messages.

## 2026-10-03 — organization workflow rebuild

- Deleted previous onboarding/scrim databases, backups, caches, logs, and obsolete docs without retaining member data.
- Split onboarding into cogs, services, storage, versioned forms, and persistent UI.
- Unified new-member/visitor applications; removed user close controls; restricted review to selected managers; corrected rejection DMs.
- Added cached-answer resets, exact role restoration including managers, owner onboarding, completion-based repair, and an instance lock.
- Organized retained scrim/provider modules and regression coverage.
- Added agent commit rules and private runtime exclusions for public source publication.
- Added the owner's explicit review override, including self-review, without granting other managers that exception.
- Automatically accept the owner's completed initial form; reset returners restore automatically without a review panel.
