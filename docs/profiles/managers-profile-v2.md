# Audience Profile — managers (v2, rich)
Replaces the 30-word v1 profile. Same profile row, enriched attributes.

```json
{
  "role": "managers",
  "display_name": "Team & Project Managers",
  "role_context": "Team and project managers who spend their days in the space between their own deliverables and other people's work. They run meetings and then owe someone a summary of them; they review drafts, estimates, and status updates produced by direct reports — increasingly with AI assistance the manager may not know about; they translate between what leadership asked for and what the team is doing. They are accountable for decisions (assignments, priorities, escalations, sign-offs) and for the accuracy of anything they pass upward, regardless of who or what drafted it. Their calendar is fragmented; their reading happens in gaps.",
  "artifacts": [
    "weekly status report to a director",
    "meeting notes to be turned into minutes and action items",
    "a direct report's draft (memo, analysis, estimate) needing review",
    "performance notes and feedback drafts",
    "project plans and schedule updates",
    "budget variance summaries",
    "escalation emails to leadership",
    "vendor or contractor quotes to compare"
  ],
  "ai_failure_modes": [
    "Delegating the decision itself: asking AI to choose the vendor, rank the team, or make the call — abdicating the judgment they are paid to exercise",
    "Approving a direct report's AI-drafted work without knowing it was AI-drafted or applying extra scrutiny — inherited hallucinations flowing upward under the manager's name",
    "Feeding performance discussions, personnel matters, or confidential team information into AI tools",
    "Sending AI-polished summaries upward without verifying the numbers and commitments inside them"
  ],
  "vocabulary": {
    "preferred": ["direct report", "stakeholder", "deliverable", "sign-off", "escalate", "action items", "one-on-one", "variance"],
    "avoid": ["synergy", "leverage (as a verb)", "circle back", "growth hacking", "sprint (unless agile context)", "human capital"]
  },
  "example_domains": [
    "turning a rambling project meeting into minutes with owners and due dates",
    "reviewing a direct report's AI-assisted cost estimate before it goes to the client",
    "comparing two contractor quotes for a services engagement",
    "drafting difficult feedback for a performance conversation (and what must never go into an AI tool about it)",
    "summarizing a month of status reports into one leadership update"
  ],
  "tone_register": "Peer-to-peer and time-respecting — an experienced colleague sharing judgment, never a trainer explaining basics. Condescension risk is high: they have sat through many mandatory trainings and will disengage at the first whiff of being talked down to.",
  "redline_emphasis": [
    "Personnel and performance information never enters unapproved tools — managers are the role most likely to violate this casually",
    "Accountability for AI-assisted work flows upward: 'the AI drafted it' and 'my report used AI' are not defenses — the sign-off is theirs"
  ],
  "reading_context": "Desktop and phone, in 10–15 minute gaps between meetings; interruptions likely. Favor tight paragraphs and clearly bounded lessons over long continuous reads.",
  "prior_knowledge": "mixed",
  "notes": "v2 2026-08: enriched from 30-word v1 per profile-template-v2; failure modes canonize the 1.3 accountability principle"
}
```
