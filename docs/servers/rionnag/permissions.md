# RIONNAG eSports: permissions

Scope: server `1554260744327929987` only. These organization, role, onboarding and
scrim rules do not apply to The Third Eye or future server profiles. Shared runtime
controls are documented in [shared operations](../../operations.md).

Guild: 1554260744327929987. Protected Rionnag role: 1555657055287648326. **Visitor** role: 1554272420959879188. Onboarding and scrim setup use this stable ID, preserving membership and overwrites across renames.

| Surface | Applicant | Visitors | Tryout/team | Selected manager | Other managers |
| --- | --- | --- | --- | --- | --- |
| Unsubmitted ticket | Own only | Own only | No | No | No |
| Submitted ticket | Own only | Own only | No | View/review | No |
| Main general/discussion | After completion | Yes | Yes | Yes | Per membership |
| Main tryout entry | No | Yes | No | No | No when on a team |
| Marvel Rivals channels | No | Read only; no voice joining | Per membership | Per membership | Per membership |
| Accept/reject | No | No | No | Another member only | No |
| Edit pending application | Own only | Own only | No | No | No |

Tickets explicitly deny other roles and allow applicant/bot members. Only the selected manager role is allowed after submission. Category permissions never grant managers access to every unsubmitted ticket. Discord owners and Administrator holders bypass overwrites; absolute owner/admin invisibility is impossible. The server owner can accept or reject any game application without a manager role. Other managers may review only another applicant for their selected game; Administrator alone does not grant review authorization.

The owner's completed form is automatically accepted even after the initial wipe, so they never need to review their own onboarding application. Accepted returners, including managers, also bypass review after completing reset forms and regain their exact saved roles.

Accepted members do not retain Visitor. Discord combines role overwrites so a Visitor allow can override team denies; this invariant hides tryout entry. Runtime checks also reject team/tryout/managers even if manually given Visitor.

Ordinary channels deny @everyone visibility. Main/Marvel category access comes from membership roles. The Bots category and its testing text and voice channels are visible to everyone, but deny member posting, thread creation, and voice connection. The bot has explicit access there. Repairs keep those settings and preserve completed members' scrim admission overwrites; unfinished members' individual visibility bypasses are removed. Managed roles and @everyone cannot be removed. Rionnag stays deliberately.

The scrim finder channel (1555989494451146802) has owner-managed overwrites and is excluded from automatic onboarding channel-permission repair. Its hidden testing visibility remains until the owner changes it; restarting or routine repair must not grant game-role visibility there. The bot keeps access to post and maintain opportunities. Owner/Administrator bypass behavior still applies.

The finder gives Marvel Rivals team, tryout, and manager roles the same read-only access. They cannot send messages, send in threads, or create threads there. The owner controls finder channel overwrites separately from onboarding repair. Eligible registered players use private buttons without Send Messages permission. Bot posting and owner/Administrator access remain available.

Marvel Rivals managers also hold the general Marvel Rivals role (the configured team role). Promotion keeps that role; reconciliation adds it to existing completed managers, and form restoration adds it to saved manager roles. General Marvel Rivals channel access therefore includes managers. Only explicit manager-specific permissions, such as application review and owner/manager commands, distinguish them.

Marvel roles: team 1554272432309797015, tryout 1554275227901632512, manager 1554281222023421992. Applications category 1555725074693099580; entry 1555730197939101737; Main category 1555732719583895622; Marvel category 1555382746992353290.

Future resets cache every removable role, including managers and unrelated assignable roles. Completion restores exact surviving IDs without needing managers who temporarily lost their role. Deleted roles require owner repair. After the initial wipe, existing game members' live Discord roles become fresh restoration metadata. Their old stored answers remain deleted; they must fill the form again. People without existing game membership need normal manager review.

Visitors may join and remain in both configured scrim voice rooms. They can speak in waiting, but cannot speak in the main scrim room and never enter its queue, lineup, or substitute pool. This exception survives onboarding permission repair.

Current single-room scrim policy supersedes the previous two-room voice policy: Visitors and game members may connect and speak in the retained Scrim room. Visitors remain excluded from queue/team/substitute composition. Tracking does not change anyone's mute state or per-member voice permissions.
