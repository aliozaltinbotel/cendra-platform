# Cendra Brain Engine — Hospitality Scenario & Pattern Learning Foundation

**Version:** 1.0  
**Purpose:** Scenario foundation for Brain Engine classifiers, agent instructions, approval flows, QA test cases, memory routing, and pattern mining.  
**Total scenario count:** 469

---

## 1. Core Objective

Brain Engine should understand short-term rental and hospitality operations as a stream of **situations**, not only as guest messages.

Every message, PMS event, channel update, owner request, vendor update, cleaner note, payment event, or review signal can become a `DecisionCase`. The system should classify the situation, inspect the right data, decide whether the agent can safely act, create a task when needed, escalate when risk exists, and decide whether the outcome should become reusable memory.

The operational goal is:

> Cendra should detect what is happening, decide what can be handled safely, ask for approval when risk exists, learn repeated PM behavior, and prevent unsafe one-off events from becoming global rules.

Brain Engine should not memorize everything. It should learn only patterns with the right scope, confidence, and safety level.

### Core Questions Brain Engine Must Ask for Every Scenario

- What stage of the guest journey is this?
- Is this a guest question, operational task, risk event, revenue opportunity, owner request, vendor update, or system conflict?
- What source of truth must be checked first?
- Is the requested action reversible, recoverable, or final?
- Is this safe to answer automatically?
- Does this require PM, owner, finance, vendor, or cleaner involvement?
- Is this missing property knowledge?
- Is this PM behavior that can become a PatternRule?
- Is this guest-specific memory?
- Is this property-specific knowledge?
- Is this owner/vendor/task workflow memory?
- Is this unsafe, one-off, or too context-specific to learn?

---

## 2. Brain Engine Interpretation Model

Brain Engine should convert hospitality operations into the following internal objects:

| Operational Input | Brain Engine Interpretation | Product Surface |
|---|---|---|
| Guest message | Scenario + intent + risk + required checks | Work item / Decision Card |
| PM approval/rejection | DecisionCase outcome | Learning candidate |
| PM edit | Strong correction signal | Teach Cendra |
| Repeated similar decisions | PatternRule candidate | Learning Center |
| Verified property fact | Property knowledge | Property Brain |
| Missing answer | Missing-info registry | Knowledge Gap card |
| Vendor delay | Vendor memory + SLA signal | Vendor workflow |
| Cleaner status | Operational workflow memory | Housekeeping state |
| Owner preference | Owner preference memory | Owner rule |
| Risky request | Blocker / Approval Required | Trust Center |
| System sync issue | Integration health event | Workflow demotion |

Brain Engine should behave as a controlled agentic layer:
1. Observe the situation.
2. Classify the scenario.
3. Inspect only relevant memory and systems.
4. Apply hard rules, blockers, safety rules, learned rules, owner preferences, then ask fallback.
5. Decide whether to auto-answer, draft, escalate, create a task, or ask for approval.
6. Log the DecisionCase.
7. Mine repeated safe patterns.
8. Ask for confirmation before applying high-risk learned rules automatically.

---

## 3. Scenario Counts

- Stage 1 — Pre-Booking / Inquiry: 50 scenarios
- Stage 2 — Booking Confirmation: 40 scenarios
- Stage 3 — Pre-Arrival: 61 scenarios
- Stage 4 — Check-In Day: 61 scenarios
- Stage 5 — During Stay: 84 scenarios
- Stage 6 — Upsell / Revenue Opportunities: 41 scenarios
- Stage 7 — Check-Out: 40 scenarios
- Stage 8 — Post-Stay: 40 scenarios
- Stage 9 — Internal Operations / Vendor / Owner Workflows: 52 scenarios

---

## 4. Risk Levels

| Risk | Meaning | Default Agent Mode |
|---|---|---|
| Low | Verified information, no money/access/safety impact | Auto-reply allowed |
| Medium | Requires data check, availability, pricing, or operational dependency | Conditional / supervised |
| High | Access, payment, policy, occupancy, refund, damage, guest manipulation, sensitive claims | PM approval |
| Critical | Safety, security, lockout, injury, active leak, gas, no water/electricity, occupied property | Immediate escalation |

---

## 5. Default Execution Modes

| Mode | Meaning |
|---|---|
| Auto-reply | Cendra can respond using verified guest-facing facts. |
| Conditional | Cendra can respond only after checking required systems and policies. |
| Draft | Cendra prepares response but PM sends. |
| Approval Required | Cendra prepares evidence and recommendation; PM/owner/finance approves. |
| Create Task | Cendra creates/updates task for cleaner, vendor, maintenance, finance, owner, or internal ops. |
| Escalate | Cendra alerts PM/on-call team; no autonomous commitment. |
| Never Auto | Cendra may summarize and collect context but cannot execute the action. |

---

## 6. Pattern Categories to Extract

### Guest Risk Patterns

Brain Engine should learn repeated combinations, not isolated labels:

- zero reviews
- local guest
- same-night booking
- one-night weekend stay
- party language
- too many visitors
- vague inquiry
- platform avoidance
- payment outside platform
- aggressive discounting
- repeated cancellation behavior
- refusal to submit ID
- refusal to pay deposit
- suspicious address/access requests
- guest books for someone else
- guest pressures for off-channel communication

**Important:** Never learn “zero-review = reject.” Learn “zero reviews + same-night + local + one-night weekend + PM rejection.”

### Pricing / Upsell Patterns

- early check-in pricing
- late checkout pricing
- pet fee behavior
- extra guest fee behavior
- discount approval behavior
- long-stay discount behavior
- extension pricing behavior
- compensation/refund behavior
- pool heating fee behavior
- parking fee behavior
- baby equipment pricing
- lost key fee
- extra cleaning fee
- transfer/activity commission behavior

### Operational SOP Patterns

- when check-in instructions are sent
- when door codes are shared
- when ID is required
- when deposit is required
- when cleaner confirmation is required
- when PM approval is needed
- when vendors are contacted
- when guest receives follow-up
- when evidence is required before compensation
- when access incidents become emergency
- when automations should demote due to integration health

### Property-Specific Patterns

- parking instructions
- difficult-to-find entrance
- recurring Wi-Fi issue
- recurring lockbox issue
- noisy neighbor
- weak AC in one room
- hot water reset process
- trash disposal instruction
- pool heating rule
- common guest confusion
- building gate behavior
- elevator reliability
- recurring amenity mismatch
- property-specific checkout instructions

### PM Behavior Patterns

- PM tends to approve early check-in if no same-day checkout
- PM rejects late checkout if next guest arrives same day
- PM offers partial refund only after photo evidence
- PM always escalates zero-review same-night bookings
- PM prefers friendly but strict language around house rules
- PM asks for payment before confirming extras
- PM requests cleaner confirmation before promising readiness
- PM uses owner approval for compensation above threshold
- PM always keeps damage claims in approval-required mode

### Vendor Patterns

- which vendor handles which issue
- vendor response time
- vendor reliability
- vendor service area
- vendor availability by time/day
- vendor requires photos first
- vendor usually resolves certain issue types
- vendor needs guest access window
- vendor quote thresholds
- vendor recurrence and no-show patterns

---

## 7. Learning Rules

Brain Engine should learn patterns cautiously.

| Confidence | Meaning | Allowed Behavior |
|---|---|---|
| 0.30 | Weak observation | Log only; do not suggest automation. |
| 0.50 | Possible pattern | Show internally as low-confidence observation. |
| 0.70 | Usable suggestion | Suggest scoped rule for PM approval. |
| 0.85 | Safe automation candidate | Eligible for semi-auto/autopilot only for low-risk reversible workflows. |
| 0.95 | Explicit SOP / confirmed rule | Can be used as confirmed SOP, subject to risk gates. |

### Learning Principles

- One PM decision is weak signal.
- Repeated similar PM decisions create strong pattern.
- High-risk PM decision can create immediate temporary rule, but not autopilot.
- Guest-specific memory should not become global property rule.
- Property-specific issue should not become company-wide rule.
- One angry guest should not define property quality.
- Repeated complaints about same amenity should become property issue pattern.
- PMS/system facts are stronger than guest claims, but active guest evidence should trigger investigation.
- PM explicit correction is very strong signal.
- AI should ask for confirmation before applying high-risk learned rules automatically.
- Explicit SOP, immutable rule, manual rule, and owner hard preference outrank learned pattern.
- When source-of-truth integration is degraded, related automation must demote.

---

## 8. Memory Types

### Property Knowledge
Stable or semi-stable facts about a specific property: access method, parking, Wi-Fi, amenities, rules, trash, appliance instructions, building quirks, recurring confusion, guest-facing facts, internal-only notes.

### PM Preference Memory
How the PM tends to decide: flexibility, tone, discount behavior, escalation style, evidence requirements, approval thresholds.

### Reservation Context Memory
Facts tied to a reservation: channel, dates, guest count, payment, verification, deposits, arrival time, checkout status, access release state.

### Guest Profile Memory
Stable guest info: repeat guest status, preferred language, prior stays, corporate traveler, family traveler, loyalty status.

### Guest Preference Memory
Preferences: quiet room, pet-friendly needs, baby equipment, remote-work needs, pillow/temperature preferences.

### Guest Risk Memory
Risk-relevant guest history: prior cancellation disputes, chargebacks, unauthorized visitors, party signals, repeated rule violations. Must be scoped and evidence-backed.

### Owner Preference Memory
Owner-level rules: no discounts, approval thresholds, blocked dates, pet policy, compensation style, direct-booking rules, reporting expectations.

### Vendor Memory
Vendor skill, service area, response time, reliability, quote behavior, photo requirements, operating hours, issue-type suitability.

### Task Workflow Memory
How tasks are created, assigned, escalated, followed up, and closed for housekeeping, maintenance, inspection, lost items, access incidents, guest complaints.

### Compensation / Refund Memory
Thresholds, evidence requirements, PM/owner/finance approvals, post-stay dispute behavior. Must never create autonomous refund behavior unless explicitly confirmed SOP and low-risk threshold.

### Channel-Specific Behavior Memory
Airbnb/Booking.com/VRBO/direct/WhatsApp/email constraints, payment limitations, cancellation/refund flows, ID/deposit process, message templates, channel-specific guardrails.

### Escalation Memory
Who handles what, on-call rules, priority triggers, severity routing, SLA windows, fallback contacts.

### Missing-Info Registry
Questions Cendra could not answer due to missing property, SOP, owner, vendor, or pricing facts.

### SOP Candidate Memory
Repeated behaviors that look like SOP but need PM confirmation.

### Confirmed SOP Memory
Explicitly approved rules, uploaded SOPs, hard rules, owner-confirmed or PM-confirmed playbooks.

---

## 9. Scenario Catalog

The following scenarios are intentionally written as operational classification units. They are not guest-message templates. Each one is meant to drive classifier labels, memory routing, approval logic, task creation, test cases, and future PatternRule mining.


# Stage 1 — Pre-Booking / Inquiry


## 1. Same-night inquiry after 22:00 from zero-review guest

### Stage
Pre-booking

### Trigger
Same-night inquiry after 22:00 from zero-review guest occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- same-night flag
- current local time
- turnover/readiness
- local guest risk

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- PMS availability
- calendar

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
How this PM handles same-night inquiry after 22:00 from zero-review guest based on review count, local status, time of day, stay length, and property risk.

### Example Learned Pattern
This PM usually escalates late same-night inquiries when guest has no reviews, is local, or asks for one-night weekend stay.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not globally reject all zero-review, local, or same-night guests; learn only the combination of risk signals and PM decision.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 2. Same-night inquiry after midnight from local guest

### Stage
Pre-booking

### Trigger
Same-night inquiry after midnight from local guest occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- same-night flag
- current local time
- turnover/readiness
- local guest risk

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- PMS availability
- calendar

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
How this PM handles same-night inquiry after midnight from local guest based on review count, local status, time of day, stay length, and property risk.

### Example Learned Pattern
This PM usually escalates late same-night inquiries when guest has no reviews, is local, or asks for one-night weekend stay.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not globally reject all zero-review, local, or same-night guests; learn only the combination of risk signals and PM decision.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 3. Zero-review guest asks one-night weekend stay

### Stage
Pre-booking

### Trigger
Zero-review guest asks one-night weekend stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- review count
- profile age
- identity verification

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
How this PM handles zero-review guest asks one-night weekend stay based on review count, local status, time of day, stay length, and property risk.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not globally reject all zero-review, local, or same-night guests; learn only the combination of risk signals and PM decision.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 4. Guest asks for discount on short stay

### Stage
Pre-booking

### Trigger
Guest asks for discount on short stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for discount on short stay, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 5. Guest asks for long-stay monthly discount

### Stage
Pre-booking

### Trigger
Guest asks for long-stay monthly discount occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for long-stay monthly discount, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 6. Guest asks to pay outside platform

### Stage
Pre-booking

### Trigger
Guest asks to pay outside platform occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- channel policy
- platform safety risk
- message intent

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Keep communication and payment inside the permitted channel; flag platform-avoidance risk.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to pay outside platform, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
Platform-avoidance requests are always answered with channel-safe language and flagged as risk.

### Memory Type
- Guest profile memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 7. Guest says calendar unavailable but asks if dates are possible

### Stage
Pre-booking

### Trigger
Guest says calendar unavailable but asks if dates are possible occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- PMS availability
- calendar

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says calendar unavailable but asks if dates are possible, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 8. Guest asks to bring more people than maximum occupancy

### Stage
Pre-booking

### Trigger
Guest asks to bring more people than maximum occupancy occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to bring more people than maximum occupancy, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 9. Guest asks for party or event permission

### Stage
Pre-booking

### Trigger
Guest asks for party or event permission occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for party or event permission, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
Party/event language triggers strict house-rule response and PM risk flag.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 10. Guest asks if visitors are allowed during stay

### Stage
Pre-booking

### Trigger
Guest asks if visitors are allowed during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if visitors are allowed during stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 11. Guest asks if pets are allowed

### Stage
Pre-booking

### Trigger
Guest asks if pets are allowed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- pet policy
- pet fee
- cleaning rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if pets are allowed, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 12. Guest asks about parking availability and cost

### Stage
Pre-booking

### Trigger
Guest asks about parking availability and cost occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- parking fact
- space availability
- permit requirement

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- PMS availability
- calendar
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks about parking availability and cost.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 13. Guest asks about pool heating availability

### Stage
Pre-booking

### Trigger
Guest asks about pool heating availability occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- PMS availability
- calendar
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks about pool heating availability.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 14. Guest asks about jacuzzi usage rules

### Stage
Pre-booking

### Trigger
Guest asks about jacuzzi usage rules occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks about jacuzzi usage rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 15. Guest asks about standard check-in time

### Stage
Pre-booking

### Trigger
Guest asks about standard check-in time occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks about standard check-in time, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 16. Guest asks for early check-in before booking

### Stage
Pre-booking

### Trigger
Guest asks for early check-in before booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for early check-in before booking, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 17. Guest asks for late checkout before booking

### Stage
Pre-booking

### Trigger
Guest asks for late checkout before booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for late checkout before booking, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 18. Guest asks distance to beach or city center

### Stage
Pre-booking

### Trigger
Guest asks distance to beach or city center occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks distance to beach or city center.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 19. Guest asks distance to airport and transfer options

### Stage
Pre-booking

### Trigger
Guest asks distance to airport and transfer options occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks distance to airport and transfer options, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 20. Guest asks if children are allowed

### Stage
Pre-booking

### Trigger
Guest asks if children are allowed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if children are allowed, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 21. Guest asks if baby cot or high chair is available

### Stage
Pre-booking

### Trigger
Guest asks if baby cot or high chair is available occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if baby cot or high chair is available, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 22. Guest asks if invoice is available before booking

### Stage
Pre-booking

### Trigger
Guest asks if invoice is available before booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if invoice is available before booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 23. Guest asks whether ID or passport is required

### Stage
Pre-booking

### Trigger
Guest asks whether ID or passport is required occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether id or passport is required, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 24. Guest asks whether security deposit is required

### Stage
Pre-booking

### Trigger
Guest asks whether security deposit is required occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 25. Guest asks whether the area is safe

### Stage
Pre-booking

### Trigger
Guest asks whether the area is safe occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether the area is safe, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 26. Guest asks for exact address before booking

### Stage
Pre-booking

### Trigger
Guest asks for exact address before booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for exact address before booking.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 27. Guest sends vague message with no dates or guest count

### Stage
Pre-booking

### Trigger
Guest sends vague message with no dates or guest count occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- PMS availability
- calendar
- house rules
- max occupancy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest sends vague message with no dates or guest count, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 28. Guest sends suspicious message asking to communicate off-platform

### Stage
Pre-booking

### Trigger
Guest sends suspicious message asking to communicate off-platform occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- channel policy
- platform safety risk
- message intent

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Keep communication and payment inside the permitted channel; flag platform-avoidance risk.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest sends suspicious message asking to communicate off-platform, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 29. Guest asks to visit the property before booking

### Stage
Pre-booking

### Trigger
Guest asks to visit the property before booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to visit the property before booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 30. Guest asks for cancellation flexibility beyond listing policy

### Stage
Pre-booking

### Trigger
Guest asks for cancellation flexibility beyond listing policy occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for cancellation flexibility beyond listing policy, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 31. Guest asks if cameras exist inside or outside property

### Stage
Pre-booking

### Trigger
Guest asks if cameras exist inside or outside property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if cameras exist inside or outside property, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 32. Guest asks about street noise, neighbors, or construction

### Stage
Pre-booking

### Trigger
Guest asks about street noise, neighbors, or construction occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks about street noise, neighbors, or construction, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 33. Guest asks about accessibility or step-free entrance

### Stage
Pre-booking

### Trigger
Guest asks about accessibility or step-free entrance occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks about accessibility or step-free entrance.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 34. Guest asks about Wi-Fi speed for remote work

### Stage
Pre-booking

### Trigger
Guest asks about Wi-Fi speed for remote work occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks about wi-fi speed for remote work.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 35. Guest asks whether heating or AC works in all rooms

### Stage
Pre-booking

### Trigger
Guest asks whether heating or AC works in all rooms occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks whether heating or ac works in all rooms.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 36. Guest asks about luggage drop-off before check-in

### Stage
Pre-booking

### Trigger
Guest asks about luggage drop-off before check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks about luggage drop-off before check-in, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 37. Guest asks whether self check-in is available

### Stage
Pre-booking

### Trigger
Guest asks whether self check-in is available occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether self check-in is available, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 38. Guest asks if smoking is allowed

### Stage
Pre-booking

### Trigger
Guest asks if smoking is allowed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if smoking is allowed, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 39. Guest asks if they can host dinner with non-staying friends

### Stage
Pre-booking

### Trigger
Guest asks if they can host dinner with non-staying friends occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if they can host dinner with non-staying friends, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 40. Guest asks for quiet hours policy

### Stage
Pre-booking

### Trigger
Guest asks for quiet hours policy occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for quiet hours policy, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 41. Guest asks for nearest public transport

### Stage
Pre-booking

### Trigger
Guest asks for nearest public transport occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for nearest public transport, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 42. Guest asks if property is suitable for elderly guest

### Stage
Pre-booking

### Trigger
Guest asks if property is suitable for elderly guest occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if property is suitable for elderly guest, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 43. Guest asks if property is suitable for business trip

### Stage
Pre-booking

### Trigger
Guest asks if property is suitable for business trip occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if property is suitable for business trip, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 44. Guest asks for split payment or installment

### Stage
Pre-booking

### Trigger
Guest asks for split payment or installment occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for split payment or installment, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 45. Guest asks to reserve without paying immediately

### Stage
Pre-booking

### Trigger
Guest asks to reserve without paying immediately occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to reserve without paying immediately, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 46. Guest asks for photos or video not in listing

### Stage
Pre-booking

### Trigger
Guest asks for photos or video not in listing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for photos or video not in listing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 47. Guest asks for building floor and elevator details

### Stage
Pre-booking

### Trigger
Guest asks for building floor and elevator details occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for building floor and elevator details.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 48. Guest asks about utility costs for monthly stay

### Stage
Pre-booking

### Trigger
Guest asks about utility costs for monthly stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks about utility costs for monthly stay, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 49. Guest asks if owner can remove cleaning fee

### Stage
Pre-booking

### Trigger
Guest asks if owner can remove cleaning fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior
- approval thresholds
- evidence pack
- channel dispute policy
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks if owner can remove cleaning fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- PM preference memory
- Guest profile memory
- Owner preference memory
- Reservation context memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 50. Guest asks if local resident can book for friend

### Stage
Pre-booking

### Trigger
Guest asks if local resident can book for friend occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested dates
- local time
- guest review count
- guest location
- guest count
- length of stay
- message tone
- listing/house rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- calendar availability
- listing rules
- guest profile/reviews
- channel policy
- property facts
- PM historical behavior

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if local resident can book for friend, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.


# Stage 2 — Booking Confirmation


## 51. Instant booking confirmed with complete guest profile

### Stage
Booking confirmation

### Trigger
Instant booking confirmed with complete guest profile occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for instant booking confirmed with complete guest profile, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 52. Booking confirmed but guest has incomplete profile

### Stage
Booking confirmation

### Trigger
Booking confirmed but guest has incomplete profile occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for booking confirmed but guest has incomplete profile, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 53. Booking confirmed and guest asks what happens next

### Stage
Booking confirmation

### Trigger
Booking confirmed and guest asks what happens next occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for booking confirmed and guest asks what happens next, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 54. Guest asks for check-in instructions immediately after booking

### Stage
Booking confirmation

### Trigger
Guest asks for check-in instructions immediately after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for check-in instructions immediately after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 55. Guest asks for full address immediately after booking

### Stage
Booking confirmation

### Trigger
Guest asks for full address immediately after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for full address immediately after booking.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 56. Guest asks for door code too early after booking

### Stage
Booking confirmation

### Trigger
Guest asks for door code too early after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for door code too early after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 57. Guest asks for invoice or receipt after booking

### Stage
Booking confirmation

### Trigger
Guest asks for invoice or receipt after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for invoice or receipt after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 58. Guest asks to change number of guests after booking

### Stage
Booking confirmation

### Trigger
Guest asks to change number of guests after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to change number of guests after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 59. Guest asks to add pet after booking

### Stage
Booking confirmation

### Trigger
Guest asks to add pet after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- pet policy
- pet fee
- cleaning rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to add pet after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 60. Guest asks to add baby cot or high chair after booking

### Stage
Booking confirmation

### Trigger
Guest asks to add baby cot or high chair after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to add baby cot or high chair after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 61. Guest asks for airport transfer after booking

### Stage
Booking confirmation

### Trigger
Guest asks for airport transfer after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for airport transfer after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 62. Guest asks for parking details after booking

### Stage
Booking confirmation

### Trigger
Guest asks for parking details after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- parking fact
- space availability
- permit requirement

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for parking details after booking.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 63. Guest asks if early check-in is possible after booking

### Stage
Booking confirmation

### Trigger
Guest asks if early check-in is possible after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks if early check-in is possible after booking, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 64. Guest asks to arrive very late after booking

### Stage
Booking confirmation

### Trigger
Guest asks to arrive very late after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to arrive very late after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 65. Guest asks for birthday or anniversary setup

### Stage
Booking confirmation

### Trigger
Guest asks for birthday or anniversary setup occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for birthday or anniversary setup, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 66. Guest asks for local recommendations after booking

### Stage
Booking confirmation

### Trigger
Guest asks for local recommendations after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for local recommendations after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 67. Payment verification issue after booking

### Stage
Booking confirmation

### Trigger
Payment verification issue after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for payment verification issue after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 68. Reservation imported from PMS with missing guest email

### Stage
Booking confirmation

### Trigger
Reservation imported from PMS with missing guest email occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when reservation imported from pms with missing guest email occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 69. Reservation imported from PMS with missing phone number

### Stage
Booking confirmation

### Trigger
Reservation imported from PMS with missing phone number occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- channel policy
- platform safety risk
- message intent
- integration health
- last sync

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when reservation imported from pms with missing phone number occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 70. Reservation imported with missing arrival time

### Stage
Booking confirmation

### Trigger
Reservation imported with missing arrival time occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for reservation imported with missing arrival time, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 71. OTA booking shows different guest count than PMS

### Stage
Booking confirmation

### Trigger
OTA booking shows different guest count than PMS occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when ota booking shows different guest count than pms occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 72. Guest changes arrival date after booking

### Stage
Booking confirmation

### Trigger
Guest changes arrival date after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest changes arrival date after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 73. Guest asks to shorten stay after booking

### Stage
Booking confirmation

### Trigger
Guest asks to shorten stay after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to shorten stay after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 74. Guest asks to extend stay after booking

### Stage
Booking confirmation

### Trigger
Guest asks to extend stay after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- PMS availability
- calendar

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to extend stay after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 75. Guest asks if deposit can be waived

### Stage
Booking confirmation

### Trigger
Guest asks if deposit can be waived occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks if deposit can be waived, without automatically applying money-impacting decisions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 76. Guest asks how to submit ID

### Stage
Booking confirmation

### Trigger
Guest asks how to submit ID occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks how to submit id, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 77. Guest refuses to submit ID required by policy

### Stage
Booking confirmation

### Trigger
Guest refuses to submit ID required by policy occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest refuses to submit id required by policy, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest risk memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 78. Guest asks to add additional vehicle

### Stage
Booking confirmation

### Trigger
Guest asks to add additional vehicle occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to add additional vehicle, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 79. Guest asks for early access to Wi-Fi password

### Stage
Booking confirmation

### Trigger
Guest asks for early access to Wi-Fi password occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for early access to wi-fi password.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 80. Guest asks for house rules summary

### Stage
Booking confirmation

### Trigger
Guest asks for house rules summary occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for house rules summary, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 81. Guest asks whether visitors are allowed after booking

### Stage
Booking confirmation

### Trigger
Guest asks whether visitors are allowed after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether visitors are allowed after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 82. Guest asks if extra towels can be prepared

### Stage
Booking confirmation

### Trigger
Guest asks if extra towels can be prepared occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if extra towels can be prepared, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 83. Guest asks for grocery delivery before arrival

### Stage
Booking confirmation

### Trigger
Guest asks for grocery delivery before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for grocery delivery before arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 84. Guest asks for remote work setup after booking

### Stage
Booking confirmation

### Trigger
Guest asks for remote work setup after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for remote work setup after booking, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 85. Guest asks for quiet room or specific unit within multi-unit property

### Stage
Booking confirmation

### Trigger
Guest asks for quiet room or specific unit within multi-unit property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for quiet room or specific unit within multi-unit property, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 86. Guest says booking was made for someone else

### Stage
Booking confirmation

### Trigger
Guest says booking was made for someone else occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says booking was made for someone else, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 87. Guest asks to communicate via WhatsApp outside OTA

### Stage
Booking confirmation

### Trigger
Guest asks to communicate via WhatsApp outside OTA occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- channel policy
- platform safety risk
- message intent
- integration health
- last sync

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when guest asks to communicate via whatsapp outside ota occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Task workflow memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 88. Guest asks for cancellation policy explanation

### Stage
Booking confirmation

### Trigger
Guest asks for cancellation policy explanation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for cancellation policy explanation, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 89. Guest says payment failed but OTA shows confirmed

### Stage
Booking confirmation

### Trigger
Guest says payment failed but OTA shows confirmed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when guest says payment failed but ota shows confirmed occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Task workflow memory
- Reservation context memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 90. Guest asks whether security cameras are present

### Stage
Booking confirmation

### Trigger
Guest asks whether security cameras are present occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- reservation status
- channel
- guest profile completeness
- payment status
- guest count
- arrival date
- required forms/deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- OTA reservation
- guest profile
- payment/verification status
- house rules
- required documents
- channel messaging constraints

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.


# Stage 3 — Pre-Arrival


## 91. Guest asks for check-in instructions one week before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for check-in instructions one week before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for check-in instructions one week before arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 92. Guest asks for check-in instructions on arrival morning

### Stage
Pre-arrival

### Trigger
Guest asks for check-in instructions on arrival morning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for check-in instructions on arrival morning, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 93. Guest says arrival time changed earlier

### Stage
Pre-arrival

### Trigger
Guest says arrival time changed earlier occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says arrival time changed earlier, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 94. Guest says arrival time changed later

### Stage
Pre-arrival

### Trigger
Guest says arrival time changed later occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says arrival time changed later, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 95. Guest arrives earlier than expected

### Stage
Pre-arrival

### Trigger
Guest arrives earlier than expected occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest arrives earlier than expected, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 96. Guest wants luggage drop-off before check-in

### Stage
Pre-arrival

### Trigger
Guest wants luggage drop-off before check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest wants luggage drop-off before check-in, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 97. Guest asks if cleaning is finished

### Stage
Pre-arrival

### Trigger
Guest asks if cleaning is finished occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if cleaning is finished, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 98. Guest asks for parking on arrival day

### Stage
Pre-arrival

### Trigger
Guest asks for parking on arrival day occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- parking fact
- space availability
- permit requirement

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for parking on arrival day.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 99. Guest asks for driving directions

### Stage
Pre-arrival

### Trigger
Guest asks for driving directions occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for driving directions, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 100. Guest cannot find the building

### Stage
Pre-arrival

### Trigger
Guest cannot find the building occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest cannot find the building, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 101. Guest asks for lockbox code

### Stage
Pre-arrival

### Trigger
Guest asks for lockbox code occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for lockbox code, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 102. Guest asks for smart lock code

### Stage
Pre-arrival

### Trigger
Guest asks for smart lock code occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state
- integration health

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when guest asks for smart lock code occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 103. Guest asks for Wi-Fi password before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for Wi-Fi password before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for wi-fi password before arrival.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 104. Guest asks for nearby supermarket

### Stage
Pre-arrival

### Trigger
Guest asks for nearby supermarket occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for nearby supermarket, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 105. Guest asks for taxi or transfer before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for taxi or transfer before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for taxi or transfer before arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 106. Guest asks if they can invite visitors

### Stage
Pre-arrival

### Trigger
Guest asks if they can invite visitors occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if they can invite visitors, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 107. Guest asks for heating instructions before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for heating instructions before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for heating instructions before arrival.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 108. Guest asks for AC instructions before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for AC instructions before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for ac instructions before arrival.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 109. Guest asks if pool is ready

### Stage
Pre-arrival

### Trigger
Guest asks if pool is ready occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks if pool is ready.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 110. Guest asks if baby cot is prepared

### Stage
Pre-arrival

### Trigger
Guest asks if baby cot is prepared occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if baby cot is prepared, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 111. Guest asks for extra towels before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for extra towels before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for extra towels before arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 112. Guest asks if they can check in after midnight

### Stage
Pre-arrival

### Trigger
Guest asks if they can check in after midnight occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- same-night flag
- current local time
- turnover/readiness
- local guest risk

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if they can check in after midnight, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
This PM usually escalates late same-night inquiries when guest has no reviews, is local, or asks for one-night weekend stay.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 113. Guest does not reply to required arrival time question

### Stage
Pre-arrival

### Trigger
Guest does not reply to required arrival time question occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest does not reply to required arrival time question, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 114. Guest has not submitted ID or passport

### Stage
Pre-arrival

### Trigger
Guest has not submitted ID or passport occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest has not submitted id or passport, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 115. Guest has not paid security deposit

### Stage
Pre-arrival

### Trigger
Guest has not paid security deposit occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 116. Guest has not signed rental agreement

### Stage
Pre-arrival

### Trigger
Guest has not signed rental agreement occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest has not signed rental agreement, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 117. PMS does not contain access code

### Stage
Pre-arrival

### Trigger
PMS does not contain access code occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state
- issue severity

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to pms does not contain access code.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Property knowledge
- Task workflow memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 118. Door code generation failed

### Stage
Pre-arrival

### Trigger
Door code generation failed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for door code generation failed, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 119. Housekeeping task not completed before arrival

### Stage
Pre-arrival

### Trigger
Housekeeping task not completed before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for housekeeping task not completed before arrival: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 120. Cleaner marked ready but inspection not completed

### Stage
Pre-arrival

### Trigger
Cleaner marked ready but inspection not completed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner marked ready but inspection not completed: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 121. Guest asks for exact address before allowed release time

### Stage
Pre-arrival

### Trigger
Guest asks for exact address before allowed release time occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state
- issue severity

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for exact address before allowed release time.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 122. Guest asks whether early check-in fee can be waived

### Stage
Pre-arrival

### Trigger
Guest asks whether early check-in fee can be waived occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks whether early check-in fee can be waived, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 123. Guest asks for key pickup by friend

### Stage
Pre-arrival

### Trigger
Guest asks for key pickup by friend occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for key pickup by friend, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 124. Guest sends invalid ID document

### Stage
Pre-arrival

### Trigger
Guest sends invalid ID document occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest sends invalid id document, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 125. Guest asks to change lead guest name

### Stage
Pre-arrival

### Trigger
Guest asks to change lead guest name occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to change lead guest name, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 126. Guest asks for crib setup confirmation

### Stage
Pre-arrival

### Trigger
Guest asks for crib setup confirmation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for crib setup confirmation, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 127. Guest asks for temperature to be pre-set

### Stage
Pre-arrival

### Trigger
Guest asks for temperature to be pre-set occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for temperature to be pre-set, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 128. Guest asks if windows have blackout curtains

### Stage
Pre-arrival

### Trigger
Guest asks if windows have blackout curtains occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks if windows have blackout curtains.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 129. Guest asks for bedding configuration change

### Stage
Pre-arrival

### Trigger
Guest asks for bedding configuration change occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for bedding configuration change, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 130. Guest asks for accessible entrance details

### Stage
Pre-arrival

### Trigger
Guest asks for accessible entrance details occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for accessible entrance details.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 131. Guest asks for public transport route

### Stage
Pre-arrival

### Trigger
Guest asks for public transport route occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for public transport route, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 132. Guest asks for luggage delivery address

### Stage
Pre-arrival

### Trigger
Guest asks for luggage delivery address occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for luggage delivery address.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 133. Guest says flight delayed and will arrive after self-check-in window

### Stage
Pre-arrival

### Trigger
Guest says flight delayed and will arrive after self-check-in window occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says flight delayed and will arrive after self-check-in window, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 134. Guest asks whether they can enter during cleaning

### Stage
Pre-arrival

### Trigger
Guest asks whether they can enter during cleaning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether they can enter during cleaning, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 135. Guest asks for special dietary grocery setup

### Stage
Pre-arrival

### Trigger
Guest asks for special dietary grocery setup occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for special dietary grocery setup, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 136. Guest asks for restaurant booking assistance

### Stage
Pre-arrival

### Trigger
Guest asks for restaurant booking assistance occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for restaurant booking assistance, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 137. Guest asks for workspace desk/chair confirmation

### Stage
Pre-arrival

### Trigger
Guest asks for workspace desk/chair confirmation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for workspace desk/chair confirmation.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 138. Guest asks to ship package to property before arrival

### Stage
Pre-arrival

### Trigger
Guest asks to ship package to property before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks to ship package to property before arrival.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 139. Guest asks if maintenance will enter during stay

### Stage
Pre-arrival

### Trigger
Guest asks if maintenance will enter during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for guest asks if maintenance will enter during stay: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Guest profile memory
- Vendor memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 140. Guest asks if there is construction nearby this week

### Stage
Pre-arrival

### Trigger
Guest asks if there is construction nearby this week occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if there is construction nearby this week, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 141. Guest asks if elevator is working

### Stage
Pre-arrival

### Trigger
Guest asks if elevator is working occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks if elevator is working.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 142. Guest asks if parking permit must be printed

### Stage
Pre-arrival

### Trigger
Guest asks if parking permit must be printed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- parking fact
- space availability
- permit requirement

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks if parking permit must be printed.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 143. Guest asks if pool heating can be turned on today

### Stage
Pre-arrival

### Trigger
Guest asks if pool heating can be turned on today occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- amenity status
- seasonality
- fee rule
- safety rules
- issue severity

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks if pool heating can be turned on today.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 144. Guest asks if pet supplies can be prepared

### Stage
Pre-arrival

### Trigger
Guest asks if pet supplies can be prepared occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- pet policy
- pet fee
- cleaning rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if pet supplies can be prepared, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 145. Guest asks if extra guest can be added last minute

### Stage
Pre-arrival

### Trigger
Guest asks if extra guest can be added last minute occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if extra guest can be added last minute, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 146. Guest asks for quiet-hours reminder for group

### Stage
Pre-arrival

### Trigger
Guest asks for quiet-hours reminder for group occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for quiet-hours reminder for group, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 147. Guest asks if remote check-in video call is available

### Stage
Pre-arrival

### Trigger
Guest asks if remote check-in video call is available occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if remote check-in video call is available, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 148. Guest asks for invoice details before arrival

### Stage
Pre-arrival

### Trigger
Guest asks for invoice details before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for invoice details before arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 149. Guest asks about city tax or tourist tax payment

### Stage
Pre-arrival

### Trigger
Guest asks about city tax or tourist tax payment occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks about city tax or tourist tax payment, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 150. Guest asks to delay security deposit payment

### Stage
Pre-arrival

### Trigger
Guest asks to delay security deposit payment occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 151. Guest asks for exact unit number before verification complete

### Stage
Pre-arrival

### Trigger
Guest asks for exact unit number before verification complete occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- time until arrival
- arrival time
- guest verification state
- payment/deposit state
- cleaning status
- access code state
- channel rules
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- PMS reservation
- arrival time
- access-code release policy
- housekeeping readiness
- security deposit
- ID/rental agreement
- property facts
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for exact unit number before verification complete.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.


# Stage 4 — Check-In Day


## 152. Guest says they are at the door

### Stage
Check-in day

### Trigger
Guest says they are at the door occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says they are at the door, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 153. Guest cannot open lockbox

### Stage
Check-in day

### Trigger
Guest cannot open lockbox occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest cannot open lockbox, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 154. Smart lock code not working

### Stage
Check-in day

### Trigger
Smart lock code not working occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when smart lock code not working occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 155. Guest cannot find key

### Stage
Check-in day

### Trigger
Guest cannot find key occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest cannot find key, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 156. Guest says address is wrong

### Stage
Check-in day

### Trigger
Guest says address is wrong occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says address is wrong.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 157. Guest says cleaner is still inside

### Stage
Check-in day

### Trigger
Guest says cleaner is still inside occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for guest says cleaner is still inside: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Guest profile memory
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 158. Guest says property is dirty

### Stage
Check-in day

### Trigger
Guest says property is dirty occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says property is dirty, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 159. Guest says bed linens are missing

### Stage
Check-in day

### Trigger
Guest says bed linens are missing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says bed linens are missing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 160. Guest says Wi-Fi not working at check-in

### Stage
Check-in day

### Trigger
Guest says Wi-Fi not working at check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says wi-fi not working at check-in.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 161. Guest says hot water not working at check-in

### Stage
Check-in day

### Trigger
Guest says hot water not working at check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says hot water not working at check-in.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 162. Guest says AC not working at check-in

### Stage
Check-in day

### Trigger
Guest says AC not working at check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says ac not working at check-in.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 163. Guest says heating not working at check-in

### Stage
Check-in day

### Trigger
Guest says heating not working at check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says heating not working at check-in.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 164. Guest says there is no electricity

### Stage
Check-in day

### Trigger
Guest says there is no electricity occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says there is no electricity, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 165. Guest says there is no water

### Stage
Check-in day

### Trigger
Guest says there is no water occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says there is no water, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 166. Guest says parking space is occupied

### Stage
Check-in day

### Trigger
Guest says parking space is occupied occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- parking fact
- space availability
- permit requirement
- issue severity

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says parking space is occupied.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 167. Guest says neighbor complained during arrival

### Stage
Check-in day

### Trigger
Guest says neighbor complained during arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says neighbor complained during arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 168. Guest says they booked wrong dates

### Stage
Check-in day

### Trigger
Guest says they booked wrong dates occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- PMS availability
- calendar

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says they booked wrong dates, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 169. Guest wants to cancel upon arrival

### Stage
Check-in day

### Trigger
Guest wants to cancel upon arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest wants to cancel upon arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 170. Guest is angry and threatens bad review

### Stage
Check-in day

### Trigger
Guest is angry and threatens bad review occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- review count
- profile age
- identity verification

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not learn that all emotional guests deserve compensation; learn escalation tone and evidence requirements only.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 171. Guest asks for compensation immediately

### Stage
Check-in day

### Trigger
Guest asks for compensation immediately occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for compensation immediately, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 172. Guest sends photo evidence of dirty property

### Stage
Check-in day

### Trigger
Guest sends photo evidence of dirty property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest sends photo evidence of dirty property, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 173. Guest sends video evidence of access issue

### Stage
Check-in day

### Trigger
Guest sends video evidence of access issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest sends video evidence of access issue.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 174. Guest arrives with more guests than booked

### Stage
Check-in day

### Trigger
Guest arrives with more guests than booked occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest arrives with more guests than booked, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 175. Guest arrives with pet without approval

### Stage
Check-in day

### Trigger
Guest arrives with pet without approval occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- pet policy
- pet fee
- cleaning rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest arrives with pet without approval, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 176. Guest wants early check-in but property is not ready

### Stage
Check-in day

### Trigger
Guest wants early check-in but property is not ready occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest wants early check-in but property is not ready, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 177. Guest cannot reach elevator

### Stage
Check-in day

### Trigger
Guest cannot reach elevator occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest cannot reach elevator.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 178. Guest cannot find building entrance

### Stage
Check-in day

### Trigger
Guest cannot find building entrance occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest cannot find building entrance.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 179. Guest says instructions are confusing

### Stage
Check-in day

### Trigger
Guest says instructions are confusing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says instructions are confusing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 180. Guest says lockbox is empty

### Stage
Check-in day

### Trigger
Guest says lockbox is empty occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says lockbox is empty, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 181. Guest says smart lock battery is dead

### Stage
Check-in day

### Trigger
Guest says smart lock battery is dead occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when guest says smart lock battery is dead occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 182. Guest says door handle is broken

### Stage
Check-in day

### Trigger
Guest says door handle is broken occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says door handle is broken, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 183. Guest says key broke in lock

### Stage
Check-in day

### Trigger
Guest says key broke in lock occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says key broke in lock, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 184. Guest says alarm is ringing

### Stage
Check-in day

### Trigger
Guest says alarm is ringing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says alarm is ringing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 185. Guest says smoke detector is beeping

### Stage
Check-in day

### Trigger
Guest says smoke detector is beeping occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says smoke detector is beeping, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 186. Guest says property smells bad

### Stage
Check-in day

### Trigger
Guest says property smells bad occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says property smells bad, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 187. Guest finds previous guest belongings

### Stage
Check-in day

### Trigger
Guest finds previous guest belongings occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest finds previous guest belongings, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 188. Guest finds pests or insects at arrival

### Stage
Check-in day

### Trigger
Guest finds pests or insects at arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest finds pests or insects at arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 189. Guest says windows or doors do not lock

### Stage
Check-in day

### Trigger
Guest says windows or doors do not lock occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says windows or doors do not lock, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 190. Guest reports safety concern at entrance

### Stage
Check-in day

### Trigger
Guest reports safety concern at entrance occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 191. Guest says unit number is wrong

### Stage
Check-in day

### Trigger
Guest says unit number is wrong occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says unit number is wrong, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 192. Guest says bed count differs from listing

### Stage
Check-in day

### Trigger
Guest says bed count differs from listing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says bed count differs from listing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 193. Guest says sofa bed is not prepared

### Stage
Check-in day

### Trigger
Guest says sofa bed is not prepared occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says sofa bed is not prepared, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 194. Guest says baby cot missing at check-in

### Stage
Check-in day

### Trigger
Guest says baby cot missing at check-in occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says baby cot missing at check-in, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 195. Guest says promised parking permit is missing

### Stage
Check-in day

### Trigger
Guest says promised parking permit is missing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- parking fact
- space availability
- permit requirement

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says promised parking permit is missing.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 196. Guest says pool is closed despite listing

### Stage
Check-in day

### Trigger
Guest says pool is closed despite listing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says pool is closed despite listing.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 197. Guest asks to move to another unit

### Stage
Check-in day

### Trigger
Guest asks to move to another unit occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to move to another unit, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 198. Guest asks for immediate refund at door

### Stage
Check-in day

### Trigger
Guest asks for immediate refund at door occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for immediate refund at door, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 199. Guest says OTA sent wrong address

### Stage
Check-in day

### Trigger
Guest says OTA sent wrong address occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says ota sent wrong address.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 200. Guest says phone number in instructions is invalid

### Stage
Check-in day

### Trigger
Guest says phone number in instructions is invalid occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- channel policy
- platform safety risk
- message intent

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says phone number in instructions is invalid, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 201. Guest cannot access building gate

### Stage
Check-in day

### Trigger
Guest cannot access building gate occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest cannot access building gate.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 202. Guest says keycard not working

### Stage
Check-in day

### Trigger
Guest says keycard not working occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says keycard not working, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 203. Guest says concierge refuses entry

### Stage
Check-in day

### Trigger
Guest says concierge refuses entry occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says concierge refuses entry, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest risk memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 204. Guest says ID verification blocks access

### Stage
Check-in day

### Trigger
Guest says ID verification blocks access occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says id verification blocks access.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 205. Guest arrives before access code release time

### Stage
Check-in day

### Trigger
Guest arrives before access code release time occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest arrives before access code release time.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 206. Guest arrives after midnight and access fails

### Stage
Check-in day

### Trigger
Guest arrives after midnight and access fails occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- same-night flag
- current local time
- turnover/readiness
- local guest risk

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest arrives after midnight and access fails.

### Example Learned Pattern
This PM usually escalates late same-night inquiries when guest has no reviews, is local, or asks for one-night weekend stay.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 207. Guest says there is someone inside property

### Stage
Check-in day

### Trigger
Guest says there is someone inside property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says there is someone inside property, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 208. Guest says property appears occupied

### Stage
Check-in day

### Trigger
Guest says property appears occupied occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says property appears occupied, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 209. Guest reports gas smell

### Stage
Check-in day

### Trigger
Guest reports gas smell occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 210. Guest reports flooding or active leak

### Stage
Check-in day

### Trigger
Guest reports flooding or active leak occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest reports flooding or active leak.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 211. Guest reports broken glass or injury hazard

### Stage
Check-in day

### Trigger
Guest reports broken glass or injury hazard occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators
- issue severity
- vendor availability
- habitability impact

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 212. Guest reports missing essential amenity

### Stage
Check-in day

### Trigger
Guest reports missing essential amenity occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- guest location
- time of day
- access method
- photo/video evidence
- urgency
- sentiment
- same-day turnover
- safety indicators

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation identity
- access-code status
- property readiness
- housekeeping status
- maintenance status
- emergency contacts
- available alternatives

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports missing essential amenity, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.


# Stage 5 — During Stay


## 213. Guest reports maintenance issue

### Stage
During stay

### Trigger
Guest reports maintenance issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- task workflow
- vendor/cleaner availability

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for guest reports maintenance issue: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 214. Guest requests extra cleaning

### Stage
During stay

### Trigger
Guest requests extra cleaning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest requests extra cleaning, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 215. Guest requests extra towels

### Stage
During stay

### Trigger
Guest requests extra towels occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest requests extra towels, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 216. Guest requests extra bedding

### Stage
During stay

### Trigger
Guest requests extra bedding occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest requests extra bedding, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 217. Guest reports noise from neighbors

### Stage
During stay

### Trigger
Guest reports noise from neighbors occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports noise from neighbors, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 218. Neighbor complains about guest noise

### Stage
During stay

### Trigger
Neighbor complains about guest noise occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for neighbor complains about guest noise, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 219. Guest asks for restaurant recommendations

### Stage
During stay

### Trigger
Guest asks for restaurant recommendations occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for restaurant recommendations, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 220. Guest asks for activity recommendations

### Stage
During stay

### Trigger
Guest asks for activity recommendations occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for activity recommendations.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 221. Guest asks how to use washing machine

### Stage
During stay

### Trigger
Guest asks how to use washing machine occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks how to use washing machine.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 222. Guest asks how to use dishwasher

### Stage
During stay

### Trigger
Guest asks how to use dishwasher occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks how to use dishwasher, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 223. Guest asks how to use stove or oven

### Stage
During stay

### Trigger
Guest asks how to use stove or oven occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks how to use stove or oven, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 224. Guest asks how to use TV or streaming device

### Stage
During stay

### Trigger
Guest asks how to use TV or streaming device occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks how to use tv or streaming device, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 225. Guest asks trash disposal rules

### Stage
During stay

### Trigger
Guest asks trash disposal rules occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks trash disposal rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 226. Guest asks pool instructions

### Stage
During stay

### Trigger
Guest asks pool instructions occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks pool instructions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 227. Guest asks jacuzzi instructions

### Stage
During stay

### Trigger
Guest asks jacuzzi instructions occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks jacuzzi instructions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 228. Guest asks for late checkout during stay

### Stage
During stay

### Trigger
Guest asks for late checkout during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- availability/pricing
- housekeeping schedule

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for late checkout during stay, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- PM preference memory
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 229. Guest asks to extend stay

### Stage
During stay

### Trigger
Guest asks to extend stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- PMS availability
- calendar

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to extend stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 230. Guest asks to shorten stay

### Stage
During stay

### Trigger
Guest asks to shorten stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to shorten stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 231. Guest reports accidental damage

### Stage
During stay

### Trigger
Guest reports accidental damage occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest reports accidental damage.

### Example Learned Pattern
Damage claims require inspection photos, reservation timeline, and PM/owner approval before guest-facing fee language.

### Memory Type
- Property knowledge

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 232. Guest breaks something and asks what to do

### Stage
During stay

### Trigger
Guest breaks something and asks what to do occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest breaks something and asks what to do, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 233. Guest loses key

### Stage
During stay

### Trigger
Guest loses key occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- access policy
- smart lock/access system

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest loses key, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 234. Guest locks themselves out

### Stage
During stay

### Trigger
Guest locks themselves out occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest locks themselves out, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 235. Guest asks for refund during stay

### Stage
During stay

### Trigger
Guest asks for refund during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- approval thresholds
- evidence pack

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for refund during stay, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 236. Guest says listing is inaccurate

### Stage
During stay

### Trigger
Guest says listing is inaccurate occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says listing is inaccurate.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 237. Guest says amenity is missing

### Stage
During stay

### Trigger
Guest says amenity is missing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says amenity is missing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 238. Guest asks for mid-stay cleaning

### Stage
During stay

### Trigger
Guest asks for mid-stay cleaning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for mid-stay cleaning, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 239. Guest asks for laundry service

### Stage
During stay

### Trigger
Guest asks for laundry service occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for laundry service, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Vendor memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 240. Guest asks for maintenance at night

### Stage
During stay

### Trigger
Guest asks for maintenance at night occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- task workflow
- vendor/cleaner availability

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for guest asks for maintenance at night: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Guest profile memory
- Vendor memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 241. Guest asks for medical help

### Stage
During stay

### Trigger
Guest asks for medical help occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 242. Guest reports safety or security concern

### Stage
During stay

### Trigger
Guest reports safety or security concern occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 243. Guest reports camera or privacy concern

### Stage
During stay

### Trigger
Guest reports camera or privacy concern occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest reports camera or privacy concern.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 244. Guest reports pest or insect issue

### Stage
During stay

### Trigger
Guest reports pest or insect issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports pest or insect issue, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 245. Guest reports bad smell or mold

### Stage
During stay

### Trigger
Guest reports bad smell or mold occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports bad smell or mold, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 246. Guest reports heating discomfort

### Stage
During stay

### Trigger
Guest reports heating discomfort occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest reports heating discomfort.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 247. Guest reports cooling discomfort

### Stage
During stay

### Trigger
Guest reports cooling discomfort occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports cooling discomfort, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 248. Guest complains about construction noise

### Stage
During stay

### Trigger
Guest complains about construction noise occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest complains about construction noise, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 249. Guest requests special local service

### Stage
During stay

### Trigger
Guest requests special local service occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest requests special local service, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Vendor memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 250. Guest sends emotional angry message

### Stage
During stay

### Trigger
Guest sends emotional angry message occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not learn that all emotional guests deserve compensation; learn escalation tone and evidence requirements only.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 251. Guest is silent after issue resolution attempt

### Stage
During stay

### Trigger
Guest is silent after issue resolution attempt occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest is silent after issue resolution attempt, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 252. Guest says Wi-Fi is slow

### Stage
During stay

### Trigger
Guest says Wi-Fi is slow occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says wi-fi is slow.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 253. Guest says Wi-Fi is completely down

### Stage
During stay

### Trigger
Guest says Wi-Fi is completely down occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says wi-fi is completely down.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 254. Guest asks for router reset instructions

### Stage
During stay

### Trigger
Guest asks for router reset instructions occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for router reset instructions, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 255. Guest says hot water intermittent

### Stage
During stay

### Trigger
Guest says hot water intermittent occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says hot water intermittent.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 256. Guest says shower drain clogged

### Stage
During stay

### Trigger
Guest says shower drain clogged occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says shower drain clogged, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 257. Guest says toilet blocked

### Stage
During stay

### Trigger
Guest says toilet blocked occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says toilet blocked, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 258. Guest says appliance tripped fuse

### Stage
During stay

### Trigger
Guest says appliance tripped fuse occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says appliance tripped fuse, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 259. Guest says power outage affects whole building

### Stage
During stay

### Trigger
Guest says power outage affects whole building occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says power outage affects whole building, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 260. Guest says water leak from ceiling

### Stage
During stay

### Trigger
Guest says water leak from ceiling occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says water leak from ceiling, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 261. Guest reports neighbor harassment

### Stage
During stay

### Trigger
Guest reports neighbor harassment occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports neighbor harassment, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 262. Guest reports suspicious person near property

### Stage
During stay

### Trigger
Guest reports suspicious person near property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports suspicious person near property, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 263. Guest asks whether they can host visitors tonight

### Stage
During stay

### Trigger
Guest asks whether they can host visitors tonight occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether they can host visitors tonight, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 264. Guest asks to add extra guest mid-stay

### Stage
During stay

### Trigger
Guest asks to add extra guest mid-stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to add extra guest mid-stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 265. Guest asks to bring pet mid-stay

### Stage
During stay

### Trigger
Guest asks to bring pet mid-stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- pet policy
- pet fee
- cleaning rules

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to bring pet mid-stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 266. Guest asks for additional parking permit

### Stage
During stay

### Trigger
Guest asks for additional parking permit occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- parking fact
- space availability
- permit requirement

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for additional parking permit.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 267. Guest asks for thermostat limit override

### Stage
During stay

### Trigger
Guest asks for thermostat limit override occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for thermostat limit override, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 268. Guest asks for pool heating extension

### Stage
During stay

### Trigger
Guest asks for pool heating extension occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- amenity status
- seasonality
- fee rule
- safety rules

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- PMS availability
- calendar

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for pool heating extension, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 269. Guest asks for early checkout and refund

### Stage
During stay

### Trigger
Guest asks for early checkout and refund occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- approval thresholds
- evidence pack

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for early checkout and refund, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 270. Guest complains about mattress comfort

### Stage
During stay

### Trigger
Guest complains about mattress comfort occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest complains about mattress comfort, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 271. Guest complains about missing kitchen item

### Stage
During stay

### Trigger
Guest complains about missing kitchen item occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest complains about missing kitchen item, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 272. Guest asks for replacement coffee capsules

### Stage
During stay

### Trigger
Guest asks for replacement coffee capsules occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for replacement coffee capsules, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 273. Guest asks for baby equipment mid-stay

### Stage
During stay

### Trigger
Guest asks for baby equipment mid-stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for baby equipment mid-stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 274. Guest asks for linen change

### Stage
During stay

### Trigger
Guest asks for linen change occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for linen change, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 275. Guest says cleaner entered without warning

### Stage
During stay

### Trigger
Guest says cleaner entered without warning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- task workflow
- vendor/cleaner availability

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for guest says cleaner entered without warning: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Guest profile memory
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 276. Guest requests privacy no-entry during stay

### Stage
During stay

### Trigger
Guest requests privacy no-entry during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest requests privacy no-entry during stay.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 277. Guest asks for local emergency number

### Stage
During stay

### Trigger
Guest asks for local emergency number occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for local emergency number, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest risk memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 278. Guest reports lost personal item during stay

### Stage
During stay

### Trigger
Guest reports lost personal item during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports lost personal item during stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 279. Guest asks to receive package at property

### Stage
During stay

### Trigger
Guest asks to receive package at property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks to receive package at property.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 280. Guest asks for invoice during stay

### Stage
During stay

### Trigger
Guest asks for invoice during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for invoice during stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 281. Guest asks for city tax explanation

### Stage
During stay

### Trigger
Guest asks for city tax explanation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for city tax explanation, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 282. Guest says card charged incorrectly

### Stage
During stay

### Trigger
Guest says card charged incorrectly occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- approval thresholds
- evidence pack

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest says card charged incorrectly, without automatically applying money-impacting decisions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 283. Guest disputes extra guest fee

### Stage
During stay

### Trigger
Guest disputes extra guest fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest disputes extra guest fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 284. Guest asks to cancel remaining nights

### Stage
During stay

### Trigger
Guest asks to cancel remaining nights occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to cancel remaining nights, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 285. Guest threatens chargeback

### Stage
During stay

### Trigger
Guest threatens chargeback occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest risk memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 286. Guest asks for owner contact

### Stage
During stay

### Trigger
Guest asks for owner contact occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for owner contact.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Property knowledge
- Guest profile memory
- Owner preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 287. Guest posts complaint in OTA thread after phone call

### Stage
During stay

### Trigger
Guest posts complaint in OTA thread after phone call occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- channel policy
- platform safety risk
- message intent
- integration health

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when guest posts complaint in ota thread after phone call occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 288. Guest changes language mid-conversation

### Stage
During stay

### Trigger
Guest changes language mid-conversation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest changes language mid-conversation, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 289. Guest sends only photo without text

### Stage
During stay

### Trigger
Guest sends only photo without text occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest sends only photo without text, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 290. Guest sends voice note describing issue

### Stage
During stay

### Trigger
Guest sends voice note describing issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest sends voice note describing issue, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 291. Guest uses sarcasm or ambiguous complaint

### Stage
During stay

### Trigger
Guest uses sarcasm or ambiguous complaint occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest uses sarcasm or ambiguous complaint, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 292. Guest asks for cannabis or prohibited activity policy

### Stage
During stay

### Trigger
Guest asks for cannabis or prohibited activity policy occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks for cannabis or prohibited activity policy.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Channel-specific behavior memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 293. Guest asks if smoking on balcony is allowed

### Stage
During stay

### Trigger
Guest asks if smoking on balcony is allowed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if smoking on balcony is allowed, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 294. Guest reports appliance sparks or electrical hazard

### Stage
During stay

### Trigger
Guest reports appliance sparks or electrical hazard occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports appliance sparks or electrical hazard, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 295. Guest reports carbon monoxide alarm

### Stage
During stay

### Trigger
Guest reports carbon monoxide alarm occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 296. Guest reports broken lock during stay

### Stage
During stay

### Trigger
Guest reports broken lock during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- issue type
- urgency
- guest sentiment
- evidence
- affected amenity
- time of day
- safety risk
- prior attempts
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- reservation status
- property SOP
- issue severity
- vendor availability
- photo/video evidence
- house rules
- guest history
- compensation policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports broken lock during stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.


# Stage 6 — Upsell / Revenue Opportunities


## 297. Early check-in request with property ready

### Stage
Upsell / revenue

### Trigger
Early check-in request with property ready occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for early check-in request with property ready, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 298. Early check-in request before cleaning complete

### Stage
Upsell / revenue

### Trigger
Early check-in request before cleaning complete occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for early check-in request before cleaning complete, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 299. Early check-in request with same-day turnover

### Stage
Upsell / revenue

### Trigger
Early check-in request with same-day turnover occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for early check-in request with same-day turnover, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM approves early check-in only if cleaning is marked ready and no same-day turnover risk exists; fee rules vary by property.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 300. Late checkout request with no same-day arrival

### Stage
Upsell / revenue

### Trigger
Late checkout request with no same-day arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for late checkout request with no same-day arrival, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 301. Late checkout request with same-day arrival

### Stage
Upsell / revenue

### Trigger
Late checkout request with same-day arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for late checkout request with same-day arrival, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 302. Late checkout request on checkout morning

### Stage
Upsell / revenue

### Trigger
Late checkout request on checkout morning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for late checkout request on checkout morning, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 303. Stay extension request with availability

### Stage
Upsell / revenue

### Trigger
Stay extension request with availability occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- PMS availability
- calendar

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for stay extension request with availability, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 304. Stay extension request but dates unavailable

### Stage
Upsell / revenue

### Trigger
Stay extension request but dates unavailable occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- PMS availability
- calendar

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for stay extension request but dates unavailable, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 305. Mid-stay cleaning request

### Stage
Upsell / revenue

### Trigger
Mid-stay cleaning request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for mid-stay cleaning request, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 306. Extra towel paid request

### Stage
Upsell / revenue

### Trigger
Extra towel paid request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for extra towel paid request, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 307. Airport transfer request

### Stage
Upsell / revenue

### Trigger
Airport transfer request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for airport transfer request, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 308. Extra guest fee request before arrival

### Stage
Upsell / revenue

### Trigger
Extra guest fee request before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- max occupancy

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for extra guest fee request before arrival, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 309. Extra guest added during stay

### Stage
Upsell / revenue

### Trigger
Extra guest added during stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for extra guest added during stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 310. Pet fee request before booking

### Stage
Upsell / revenue

### Trigger
Pet fee request before booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- pet policy

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for pet fee request before booking, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 311. Pet fee request after booking

### Stage
Upsell / revenue

### Trigger
Pet fee request after booking occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- pet policy

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for pet fee request after booking, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 312. Pool heating fee request

### Stage
Upsell / revenue

### Trigger
Pool heating fee request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- amenity status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact
- approval thresholds

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for pool heating fee request, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 313. Parking fee request

### Stage
Upsell / revenue

### Trigger
Parking fee request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- parking fact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact
- approval thresholds

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for parking fee request, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 314. Baby equipment fee request

### Stage
Upsell / revenue

### Trigger
Baby equipment fee request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for baby equipment fee request, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 315. Romantic setup request

### Stage
Upsell / revenue

### Trigger
Romantic setup request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for romantic setup request, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 316. Birthday setup request

### Stage
Upsell / revenue

### Trigger
Birthday setup request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for birthday setup request, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 317. Local tour or activity request

### Stage
Upsell / revenue

### Trigger
Local tour or activity request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to local tour or activity request.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 318. Breakfast or grocery package request

### Stage
Upsell / revenue

### Trigger
Breakfast or grocery package request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to breakfast or grocery package request.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 319. Damage deposit collection

### Stage
Upsell / revenue

### Trigger
Damage deposit collection occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for damage deposit collection, without automatically applying money-impacting decisions.

### Example Learned Pattern
Damage claims require inspection photos, reservation timeline, and PM/owner approval before guest-facing fee language.

### Memory Type
- PM preference memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 320. Lost key fee assessment

### Stage
Upsell / revenue

### Trigger
Lost key fee assessment occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- access release policy

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for lost key fee assessment, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 321. Replacement key delivery fee

### Stage
Upsell / revenue

### Trigger
Replacement key delivery fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- access release policy

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for replacement key delivery fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 322. Extra cleaning fee after excessive mess

### Stage
Upsell / revenue

### Trigger
Extra cleaning fee after excessive mess occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- housekeeping task status

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for extra cleaning fee after excessive mess, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 323. Late checkout upsell proactive offer

### Stage
Upsell / revenue

### Trigger
Late checkout upsell proactive offer occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for late checkout upsell proactive offer, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- Property knowledge
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 324. Orphan night extension offer

### Stage
Upsell / revenue

### Trigger
Orphan night extension offer occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- PMS availability
- calendar

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for orphan night extension offer, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 325. Room or unit upgrade offer

### Stage
Upsell / revenue

### Trigger
Room or unit upgrade offer occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for room or unit upgrade offer, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 326. Early luggage drop-off paid offer

### Stage
Upsell / revenue

### Trigger
Early luggage drop-off paid offer occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for early luggage drop-off paid offer, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 327. Luggage storage after checkout paid offer

### Stage
Upsell / revenue

### Trigger
Luggage storage after checkout paid offer occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for luggage storage after checkout paid offer, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 328. Workspace setup upsell

### Stage
Upsell / revenue

### Trigger
Workspace setup upsell occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for workspace setup upsell, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 329. Weekly cleaning package for monthly stay

### Stage
Upsell / revenue

### Trigger
Weekly cleaning package for monthly stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- housekeeping task status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for weekly cleaning package for monthly stay, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 330. Pet cleaning supplement

### Stage
Upsell / revenue

### Trigger
Pet cleaning supplement occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- pet policy
- pet fee
- cleaning rules
- housekeeping task status
- cleaner ETA

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for pet cleaning supplement, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 331. Additional parking permit fee

### Stage
Upsell / revenue

### Trigger
Additional parking permit fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history
- parking fact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact
- approval thresholds

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for additional parking permit fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 332. EV charging fee request

### Stage
Upsell / revenue

### Trigger
EV charging fee request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for ev charging fee request, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 333. Beach equipment rental

### Stage
Upsell / revenue

### Trigger
Beach equipment rental occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to beach equipment rental.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 334. Baby stroller rental

### Stage
Upsell / revenue

### Trigger
Baby stroller rental occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for baby stroller rental, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 335. Ski equipment storage fee

### Stage
Upsell / revenue

### Trigger
Ski equipment storage fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for ski equipment storage fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 336. Private chef request

### Stage
Upsell / revenue

### Trigger
Private chef request occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for private chef request, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 337. Airport transfer cancellation fee

### Stage
Upsell / revenue

### Trigger
Airport transfer cancellation fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- requested add-on
- dates/times
- inventory availability
- guest segment
- fee policy
- payment status
- same-day arrival/departure
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- availability
- pricing rule
- calendar gaps
- payment method
- channel policy
- owner/PM pricing behavior
- fulfillment capacity
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for airport transfer cancellation fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.


# Stage 7 — Check-Out


## 338. Guest asks for checkout instructions

### Stage
Check-out

### Trigger
Guest asks for checkout instructions occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for checkout instructions, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 339. Guest asks for late checkout on checkout day

### Stage
Check-out

### Trigger
Guest asks for late checkout on checkout day occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for late checkout on checkout day, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM rejects late checkout when same-day arrival exists; otherwise offers paid late checkout after checking cleaning schedule.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 340. Guest leaves late

### Stage
Check-out

### Trigger
Guest leaves late occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- availability/pricing
- housekeeping schedule
- same-day arrival/departure

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest leaves late, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 341. Guest asks where to leave keys

### Stage
Check-out

### Trigger
Guest asks where to leave keys occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks where to leave keys, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 342. Guest forgets items

### Stage
Check-out

### Trigger
Guest forgets items occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest forgets items, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 343. Guest asks luggage storage after checkout

### Stage
Check-out

### Trigger
Guest asks luggage storage after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks luggage storage after checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 344. Guest reports issue right before checkout

### Stage
Check-out

### Trigger
Guest reports issue right before checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports issue right before checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 345. Guest says they cleaned the property

### Stage
Check-out

### Trigger
Guest says they cleaned the property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says they cleaned the property, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 346. Guest asks for refund after stay

### Stage
Check-out

### Trigger
Guest asks for refund after stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for refund after stay, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 347. Guest asks for invoice at checkout

### Stage
Check-out

### Trigger
Guest asks for invoice at checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for invoice at checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 348. Guest asks about deposit return

### Stage
Check-out

### Trigger
Guest asks about deposit return occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks about deposit return, without automatically applying money-impacting decisions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 349. Guest damages property before checkout

### Stage
Check-out

### Trigger
Guest damages property before checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest damages property before checkout, without automatically applying money-impacting decisions.

### Example Learned Pattern
Damage claims require inspection photos, reservation timeline, and PM/owner approval before guest-facing fee language.

### Memory Type
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 350. Cleaner reports damage

### Stage
Check-out

### Trigger
Cleaner reports damage occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence
- compensation threshold
- evidence strength

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports damage: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 351. Cleaner reports excessive mess

### Stage
Check-out

### Trigger
Cleaner reports excessive mess occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports excessive mess: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 352. Cleaner reports missing items

### Stage
Check-out

### Trigger
Cleaner reports missing items occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports missing items: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 353. Guest disputes damage at checkout

### Stage
Check-out

### Trigger
Guest disputes damage at checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest disputes damage at checkout, without automatically applying money-impacting decisions.

### Example Learned Pattern
Damage claims require inspection photos, reservation timeline, and PM/owner approval before guest-facing fee language.

### Memory Type
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 354. Next guest has same-day arrival

### Stage
Check-out

### Trigger
Next guest has same-day arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for next guest has same-day arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 355. Housekeeping schedule conflict

### Stage
Check-out

### Trigger
Housekeeping schedule conflict occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for housekeeping schedule conflict: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 356. Guest does not check out on time

### Stage
Check-out

### Trigger
Guest does not check out on time occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest does not check out on time, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 357. Guest asks to stay additional few hours without paying

### Stage
Check-out

### Trigger
Guest asks to stay additional few hours without paying occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to stay additional few hours without paying, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 358. Guest says flight delayed and wants to remain

### Stage
Check-out

### Trigger
Guest says flight delayed and wants to remain occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says flight delayed and wants to remain, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 359. Guest cannot lock door when leaving

### Stage
Check-out

### Trigger
Guest cannot lock door when leaving occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest cannot lock door when leaving, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 360. Guest leaves keys in wrong place

### Stage
Check-out

### Trigger
Guest leaves keys in wrong place occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- access release policy
- guest verification
- arrival date/time
- smart lock state
- issue severity

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest leaves keys in wrong place.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 361. Guest takes keys accidentally

### Stage
Check-out

### Trigger
Guest takes keys accidentally occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- access release policy
- guest verification
- arrival date/time
- smart lock state
- issue severity

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest takes keys accidentally.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 362. Guest leaves luggage inside after checkout

### Stage
Check-out

### Trigger
Guest leaves luggage inside after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest leaves luggage inside after checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 363. Guest leaves trash or dishes

### Stage
Check-out

### Trigger
Guest leaves trash or dishes occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest leaves trash or dishes.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 364. Guest reports lost item while leaving

### Stage
Check-out

### Trigger
Guest reports lost item while leaving occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest reports lost item while leaving, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 365. Cleaner cannot access property after checkout

### Stage
Check-out

### Trigger
Cleaner cannot access property after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence
- issue severity
- vendor availability

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- property knowledge
- last verified fact
- task workflow

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to cleaner cannot access property after checkout.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Property knowledge
- Task workflow memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 366. Smart lock code did not expire

### Stage
Check-out

### Trigger
Smart lock code did not expire occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- access release policy
- guest verification
- arrival date/time
- smart lock state
- integration health

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when smart lock code did not expire occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 367. Access code remains active after checkout

### Stage
Check-out

### Trigger
Access code remains active after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- access release policy
- guest verification
- arrival date/time
- smart lock state
- issue severity

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to access code remains active after checkout.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Property knowledge
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 368. Guest asks for checkout extension because child is sick

### Stage
Check-out

### Trigger
Guest asks for checkout extension because child is sick occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- PMS availability
- calendar

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for checkout extension because child is sick, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 369. Guest asks for taxi after checkout

### Stage
Check-out

### Trigger
Guest asks for taxi after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for taxi after checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 370. Guest asks where to dispose trash

### Stage
Check-out

### Trigger
Guest asks where to dispose trash occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest asks where to dispose trash.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 371. Guest asks whether linens should be stripped

### Stage
Check-out

### Trigger
Guest asks whether linens should be stripped occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks whether linens should be stripped, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 372. Guest asks whether deposit is held for cleaning

### Stage
Check-out

### Trigger
Guest asks whether deposit is held for cleaning occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence
- compensation threshold
- evidence strength

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks whether deposit is held for cleaning, without automatically applying money-impacting decisions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 373. Guest says neighbor complained during departure

### Stage
Check-out

### Trigger
Guest says neighbor complained during departure occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says neighbor complained during departure, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 374. Guest leaves pet mess

### Stage
Check-out

### Trigger
Guest leaves pet mess occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- pet policy
- pet fee
- cleaning rules

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest leaves pet mess, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 375. Guest leaves unauthorized extra guests overnight

### Stage
Check-out

### Trigger
Guest leaves unauthorized extra guests overnight occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- house rules
- max occupancy
- reservation guest count

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest leaves unauthorized extra guests overnight, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 376. Cleaner reports smoking smell

### Stage
Check-out

### Trigger
Cleaner reports smoking smell occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports smoking smell: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Property knowledge
- Task workflow memory
- Reservation context memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 377. Cleaner reports missing remote or small item

### Stage
Check-out

### Trigger
Cleaner reports missing remote or small item occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- checkout date/time
- same-day arrival
- cleaner ETA
- guest status
- keys/access state
- photos/reports
- deposit exposure
- housekeeping task status
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- checkout time
- next booking
- cleaning schedule
- access-code expiry
- deposit policy
- inspection report
- lost item process
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports missing remote or small item: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory
- Reservation context memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.


# Stage 8 — Post-Stay


## 378. Guest leaves good review

### Stage
Post-stay

### Trigger
Guest leaves good review occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest leaves good review, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 379. Guest leaves bad review

### Stage
Post-stay

### Trigger
Guest leaves bad review occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest leaves bad review, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not learn that all emotional guests deserve compensation; learn escalation tone and evidence requirements only.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 380. Guest privately complains after checkout

### Stage
Post-stay

### Trigger
Guest privately complains after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest privately complains after checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- SOP candidate memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 381. Guest asks for refund after checkout

### Stage
Post-stay

### Trigger
Guest asks for refund after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for refund after checkout, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 382. Guest asks to book again

### Stage
Post-stay

### Trigger
Guest asks to book again occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to book again, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 383. Guest asks for direct booking discount

### Stage
Post-stay

### Trigger
Guest asks for direct booking discount occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest asks for direct booking discount, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 384. Guest disputes damage claim

### Stage
Post-stay

### Trigger
Guest disputes damage claim occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest disputes damage claim, without automatically applying money-impacting decisions.

### Example Learned Pattern
Damage claims require inspection photos, reservation timeline, and PM/owner approval before guest-facing fee language.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 385. Guest asks about lost item after checkout

### Stage
Post-stay

### Trigger
Guest asks about lost item after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks about lost item after checkout, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- SOP candidate memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 386. PM wants to request review

### Stage
Post-stay

### Trigger
PM wants to request review occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for pm wants to request review, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 387. PM wants to respond to public review

### Stage
Post-stay

### Trigger
PM wants to respond to public review occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for pm wants to respond to public review, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 388. Guest mentions recurring property issue

### Stage
Post-stay

### Trigger
Guest mentions recurring property issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest mentions recurring property issue, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 389. Guest provides useful local recommendation feedback

### Stage
Post-stay

### Trigger
Guest provides useful local recommendation feedback occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- rate plan
- stay length
- floor price
- PM discount history
- issue severity

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- property knowledge
- last verified fact
- approval thresholds

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest provides useful local recommendation feedback, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- PM preference memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 390. Guest asks for invoice later

### Stage
Post-stay

### Trigger
Guest asks for invoice later occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for invoice later, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 391. Guest wants returning guest discount

### Stage
Post-stay

### Trigger
Guest wants returning guest discount occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest wants returning guest discount, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 392. Guest asks for deposit return status

### Stage
Post-stay

### Trigger
Guest asks for deposit return status occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for deposit return status, without automatically applying money-impacting decisions.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Guest profile memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 393. Guest claims item was missing before arrival

### Stage
Post-stay

### Trigger
Guest claims item was missing before arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest claims item was missing before arrival, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Missing-info registry

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 394. Guest says cleaning issue ruined stay after departure

### Stage
Post-stay

### Trigger
Guest says cleaning issue ruined stay after departure occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says cleaning issue ruined stay after departure, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 395. Guest asks for compensation after bad review

### Stage
Post-stay

### Trigger
Guest asks for compensation after bad review occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification
- compensation threshold
- evidence strength

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest asks for compensation after bad review, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not learn that all emotional guests deserve compensation; learn escalation tone and evidence requirements only.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 396. Guest threatens chargeback after checkout

### Stage
Post-stay

### Trigger
Guest threatens chargeback after checkout occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- issue severity
- vendor availability
- photo/video evidence
- habitability impact
- compensation threshold

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- property knowledge
- last verified fact
- approval thresholds

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest risk memory
- SOP candidate memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 397. Guest says they will contact OTA support

### Stage
Post-stay

### Trigger
Guest says they will contact OTA support occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- issue severity
- vendor availability
- photo/video evidence
- habitability impact
- integration health

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest says they will contact ota support.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Task workflow memory
- Channel-specific behavior memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 398. Guest leaves private note about weak Wi-Fi

### Stage
Post-stay

### Trigger
Guest leaves private note about weak Wi-Fi occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest leaves private note about weak wi-fi.

### Example Learned Pattern
Wi-Fi issues follow router reset first, then photo of router lights, then maintenance escalation if unresolved.

### Memory Type
- Property knowledge

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 399. Guest says neighbor noise happened every night

### Stage
Post-stay

### Trigger
Guest says neighbor noise happened every night occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says neighbor noise happened every night, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 400. Guest asks if property will be available next month

### Stage
Post-stay

### Trigger
Guest asks if property will be available next month occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks if property will be available next month, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 401. Guest asks for corporate invoice details

### Stage
Post-stay

### Trigger
Guest asks for corporate invoice details occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Conditional

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for corporate invoice details, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 402. Guest requests review removal or change

### Stage
Post-stay

### Trigger
Guest requests review removal or change occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest requests review removal or change, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 403. Owner asks why review score dropped

### Stage
Post-stay

### Trigger
Owner asks why review score dropped occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- review count
- profile age
- identity verification
- owner preference
- owner approval SLA

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- owner preference memory
- approval authority

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner asks why review score dropped.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 404. Guest left low rating but positive text

### Stage
Post-stay

### Trigger
Guest left low rating but positive text occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest left low rating but positive text, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 405. Guest left positive rating but private complaint

### Stage
Post-stay

### Trigger
Guest left positive rating but private complaint occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest left positive rating but private complaint, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 406. Guest asks for receipt for extras

### Stage
Post-stay

### Trigger
Guest asks for receipt for extras occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for receipt for extras, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 407. Guest asks shipping cost for lost item

### Stage
Post-stay

### Trigger
Guest asks shipping cost for lost item occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks shipping cost for lost item, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 408. Guest disputes lost key fee

### Stage
Post-stay

### Trigger
Guest disputes lost key fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- rate plan
- stay length
- floor price
- PM discount history
- access release policy

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for guest disputes lost key fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 409. Cleaner finds high-value item

### Stage
Post-stay

### Trigger
Cleaner finds high-value item occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner finds high-value item: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 410. Guest asks for photos of found item

### Stage
Post-stay

### Trigger
Guest asks for photos of found item occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks for photos of found item, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 411. Guest says refund promised by staff

### Stage
Post-stay

### Trigger
Guest says refund promised by staff occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for guest says refund promised by staff, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 412. Guest reports injury after stay

### Stage
Post-stay

### Trigger
Guest reports injury after stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Critical

### AI Default Behavior
Treat as immediate operational incident; acknowledge safely, collect minimal evidence, and escalate to PM/on-call team before making commitments.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not generalize emergency handling beyond escalation SOP and emergency contact workflow.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 413. Guest reports privacy concern after stay

### Stage
Post-stay

### Trigger
Guest reports privacy concern after stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to guest reports privacy concern after stay.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 414. Guest reports suspected camera after stay

### Stage
Post-stay

### Trigger
Guest reports suspected camera after stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
No

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM behavior memory
- Operational workflow memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 415. Guest wants loyalty offer for repeat stay

### Stage
Post-stay

### Trigger
Guest wants loyalty offer for repeat stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest wants loyalty offer for repeat stay, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 416. Guest asks to block same unit for future dates

### Stage
Post-stay

### Trigger
Guest asks to block same unit for future dates occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history
- PMS availability
- calendar

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest asks to block same unit for future dates, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.

## 417. Guest says amenities should be updated in listing

### Stage
Post-stay

### Trigger
Guest says amenities should be updated in listing occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- review sentiment
- refund request
- repeat guest potential
- evidence
- issue recurrence
- public/private channel
- timeline since checkout

### Risk Level
Low

### AI Default Behavior
Answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

### Required Data Checks
- review text/rating
- private feedback
- reservation record
- damage/inspection evidence
- deposit/fee records
- guest history
- property issue history

### Should AI Auto-Reply?
Yes

### Should AI Escalate to PM?
No

### Should AI Create Task?
No

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for guest says amenities should be updated in listing, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory

### What Not to Learn
Do not let one post-stay complaint redefine property quality; require repeated complaints or inspection confirmation.

### Future Behavior Impact
Next time, Cendra can answer faster using verified knowledge, while still logging the decision and creating a missing-info card if confidence drops.


# Stage 9 — Internal Operations / Vendor / Owner Workflows


## 418. Cleaner task not completed before ETA

### Stage
Internal operations

### Trigger
Cleaner task not completed before ETA occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner task not completed before eta: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 419. Cleaner marks property ready

### Stage
Internal operations

### Trigger
Cleaner marks property ready occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner marks property ready: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 420. Inspector finds issue after cleaner ready

### Stage
Internal operations

### Trigger
Inspector finds issue after cleaner ready occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for inspector finds issue after cleaner ready: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 421. Maintenance vendor accepts task

### Stage
Internal operations

### Trigger
Maintenance vendor accepts task occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact
- task workflow

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to maintenance vendor accepts task.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Property knowledge
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 422. Maintenance vendor does not respond

### Stage
Internal operations

### Trigger
Maintenance vendor does not respond occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for maintenance vendor does not respond: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 423. Vendor asks for more information

### Stage
Internal operations

### Trigger
Vendor asks for more information occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- vendor SLA
- vendor reliability
- required evidence
- quote threshold

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for vendor asks for more information: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 424. Vendor changes arrival time

### Stage
Internal operations

### Trigger
Vendor changes arrival time occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- vendor SLA
- vendor reliability
- required evidence
- quote threshold

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for vendor changes arrival time: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 425. Vendor says issue is resolved

### Stage
Internal operations

### Trigger
Vendor says issue is resolved occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- vendor SLA
- vendor reliability
- required evidence
- quote threshold

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for vendor says issue is resolved: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 426. Guest says vendor issue is not resolved

### Stage
Internal operations

### Trigger
Guest says vendor issue is not resolved occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- vendor SLA
- vendor reliability
- required evidence
- quote threshold

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for guest says vendor issue is not resolved: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Guest profile memory
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 427. Owner asks about current reservation

### Stage
Internal operations

### Trigger
Owner asks about current reservation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner asks about current reservation.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 428. Owner blocks dates

### Stage
Internal operations

### Trigger
Owner blocks dates occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- PMS availability
- calendar
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner blocks dates.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 429. Owner asks revenue question

### Stage
Internal operations

### Trigger
Owner asks revenue question occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner asks revenue question.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 430. PM changes SOP manually

### Stage
Internal operations

### Trigger
PM changes SOP manually occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for pm changes sop manually, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 431. PMS data conflicts with guest message

### Stage
Internal operations

### Trigger
PMS data conflicts with guest message occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when pms data conflicts with guest message occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 432. Channel message conflicts with PMS reservation

### Stage
Internal operations

### Trigger
Channel message conflicts with PMS reservation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when channel message conflicts with pms reservation occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Channel-specific behavior memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 433. AI has missing property knowledge

### Stage
Internal operations

### Trigger
AI has missing property knowledge occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for ai has missing property knowledge, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 434. AI finds repeated missing info

### Stage
Internal operations

### Trigger
AI finds repeated missing info occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for ai finds repeated missing info, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Guest profile memory
- Task workflow memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 435. Cleaner reports inventory low

### Stage
Internal operations

### Trigger
Cleaner reports inventory low occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports inventory low: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 436. Cleaner reports damage before guest arrival

### Stage
Internal operations

### Trigger
Cleaner reports damage before guest arrival occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner reports damage before guest arrival: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 437. Cleaner cannot access property

### Stage
Internal operations

### Trigger
Cleaner cannot access property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact
- task workflow

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to cleaner cannot access property.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Property knowledge
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 438. Cleaner asks for updated access code

### Stage
Internal operations

### Trigger
Cleaner asks for updated access code occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to cleaner asks for updated access code.

### Example Learned Pattern
Access-sensitive information is released only after verification and configured timing; early requests stay approval-blocked.

### Memory Type
- Property knowledge
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 439. Cleaner is delayed due to previous job

### Stage
Internal operations

### Trigger
Cleaner is delayed due to previous job occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for cleaner is delayed due to previous job: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 440. Inspector approves property with minor issue

### Stage
Internal operations

### Trigger
Inspector approves property with minor issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for inspector approves property with minor issue, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 441. Vendor quote exceeds approval threshold

### Stage
Internal operations

### Trigger
Vendor quote exceeds approval threshold occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- vendor SLA
- vendor reliability
- required evidence
- quote threshold

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for vendor quote exceeds approval threshold: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 442. Vendor requests guest phone number

### Stage
Internal operations

### Trigger
Vendor requests guest phone number occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- channel policy
- platform safety risk
- message intent
- vendor SLA

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for vendor requests guest phone number: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 443. Vendor cannot enter property due to guest privacy

### Stage
Internal operations

### Trigger
Vendor cannot enter property due to guest privacy occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact
- task workflow

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to vendor cannot enter property due to guest privacy.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Property knowledge
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 444. Vendor completes repair but no invoice attached

### Stage
Internal operations

### Trigger
Vendor completes repair but no invoice attached occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact
- task workflow

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to vendor completes repair but no invoice attached.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Property knowledge
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 445. Owner requests no discounts for specific property

### Stage
Internal operations

### Trigger
Owner requests no discounts for specific property occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for owner requests no discounts for specific property, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 446. Owner allows special discount for repeat guests

### Stage
Internal operations

### Trigger
Owner allows special discount for repeat guests occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for owner allows special discount for repeat guests, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
This PM offers long-stay discounts only when stay length exceeds 14 nights and occupancy forecast is weak; short-stay discounts are usually rejected.

### Memory Type
- PM preference memory
- Guest profile memory
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 447. Owner asks to change house rule

### Stage
Internal operations

### Trigger
Owner asks to change house rule occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner asks to change house rule.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 448. Owner asks to block dates for personal stay

### Stage
Internal operations

### Trigger
Owner asks to block dates for personal stay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- PMS availability
- calendar
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner asks to block dates for personal stay.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 449. Owner asks for incident summary

### Stage
Internal operations

### Trigger
Owner asks for incident summary occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for owner asks for incident summary.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 450. Owner disputes compensation decision

### Stage
Internal operations

### Trigger
Owner disputes compensation decision occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for owner disputes compensation decision, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 451. Finance needs approval for refund

### Stage
Internal operations

### Trigger
Finance needs approval for refund occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Approval and evidence pattern for finance needs approval for refund, without automatically applying money-impacting decisions.

### Example Learned Pattern
This PM only considers compensation after evidence, impact assessment, and approval; AI must not promise money.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not learn automatic refund amounts from a single case; money decisions require explicit SOP or approval thresholds.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 452. Finance flags unpaid extra fee

### Stage
Internal operations

### Trigger
Finance flags unpaid extra fee occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- rate plan
- stay length
- floor price
- PM discount history

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
PM/property-specific pricing and approval behavior for finance flags unpaid extra fee, including thresholds, availability checks, and payment-before-confirmation rules.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 453. PMS calendar sync fails

### Stage
Internal operations

### Trigger
PMS calendar sync fails occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- PMS availability
- calendar

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when pms calendar sync fails occurs.

### Example Learned Pattern
When source-of-truth sync is degraded, related workflows move from autopilot to approval-required.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 454. OTA reservation imported twice

### Stage
Internal operations

### Trigger
OTA reservation imported twice occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- integration health
- last sync
- affected workflows
- fallback mode

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when ota reservation imported twice occurs.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 455. Airbnb message arrives without mapped reservation

### Stage
Internal operations

### Trigger
Airbnb message arrives without mapped reservation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for airbnb message arrives without mapped reservation, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 456. Booking.com virtual card issue

### Stage
Internal operations

### Trigger
Booking.com virtual card issue occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for booking.com virtual card issue, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Channel-specific behavior memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 457. WhatsApp guest not linked to reservation

### Stage
Internal operations

### Trigger
WhatsApp guest not linked to reservation occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- channel policy
- platform safety risk
- message intent

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for whatsapp guest not linked to reservation, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 458. Smart lock integration down

### Stage
Internal operations

### Trigger
Smart lock integration down occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- access release policy
- guest verification
- arrival date/time
- smart lock state

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Never reveal access credentials before verification and release policy; if access fails, create an urgent access incident. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- access policy
- smart lock/access system
- guest verification

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when smart lock integration down occurs.

### Example Learned Pattern
When source-of-truth sync is degraded, related workflows move from autopilot to approval-required.

### Memory Type
- Property knowledge
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 459. Cleaning system integration down

### Stage
Internal operations

### Trigger
Cleaning system integration down occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Downgrade affected automations, show fallback mode, and create integration-health alert.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Fallback and automation-demotion behavior when cleaning system integration down occurs.

### Example Learned Pattern
When source-of-truth sync is degraded, related workflows move from autopilot to approval-required.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 460. Payment link failed

### Stage
Internal operations

### Trigger
Payment link failed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for payment link failed, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 461. Security deposit provider failed

### Stage
Internal operations

### Trigger
Security deposit provider failed occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- compensation threshold
- evidence strength
- OTA dispute window
- approval authority

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Prepare evidence and safe response; never offer or accept money-impacting decisions without the configured approval path.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- approval thresholds
- evidence pack
- channel dispute policy

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
No

### Pattern to Learn
No durable pattern by default; keep only incident/action trace unless PM explicitly confirms a reusable SOP.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- PM preference memory
- Task workflow memory

### What Not to Learn
Do not learn guest liability from unverified claims; require evidence and approval.

### Future Behavior Impact
Next time, Cendra should recognize the incident class faster, follow the safe escalation/task workflow, and avoid permanent behavioral changes unless PM confirms an SOP.

## 462. Channel policy prevents requested action

### Stage
Internal operations

### Trigger
Channel policy prevents requested action occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- max occupancy
- house rules
- local regulations
- security deposit

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved. Enforce occupancy/visitor/party rules; never imply permission that violates listing rules or local regulation. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to channel policy prevents requested action.

### Example Learned Pattern
Party/event language triggers strict house-rule response and PM risk flag.

### Memory Type
- Property knowledge
- Task workflow memory
- Channel-specific behavior memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 463. Property listing data conflicts with SOP

### Stage
Internal operations

### Trigger
Property listing data conflicts with SOP occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
High

### AI Default Behavior
Do not resolve purely from language. Build an evidence-backed decision card, enforce house/channel rules, and route to PM approval when money, access, safety, or policy is involved.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
No

### Should AI Escalate to PM?
Yes

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for property listing data conflicts with sop, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Task workflow memory
- Missing-info registry
- SOP candidate memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should surface the same risk pattern earlier, prepare evidence and draft safely, but keep execution behind PM approval.

## 464. Recurring vendor delay pattern detected

### Stage
Internal operations

### Trigger
Recurring vendor delay pattern detected occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- vendor SLA
- vendor reliability
- required evidence
- quote threshold

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for recurring vendor delay pattern detected: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
This issue type is routed to the preferred vendor with required photos; PM approval is required when quote exceeds threshold.

### Memory Type
- Vendor memory
- Task workflow memory

### What Not to Learn
Do not infer vendor reliability from one delayed response; require repeated evidence.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 465. Recurring cleaner quality issue detected

### Stage
Internal operations

### Trigger
Recurring cleaner quality issue detected occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- housekeeping task status
- cleaner ETA
- inspection state
- photo evidence

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Create or update housekeeping task, ask for photos when useful, and keep guest informed without promising compensation.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- task workflow
- vendor/cleaner availability
- SLA

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Operational workflow pattern for recurring cleaner quality issue detected: who handles it, SLA, evidence needed, escalation path, and follow-up style.

### Example Learned Pattern
Cleaning issues require photo evidence, housekeeping task creation, and guest follow-up after cleaner confirms completion.

### Memory Type
- Task workflow memory

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 466. Repeated access-code confusion detected

### Stage
Internal operations

### Trigger
Repeated access-code confusion detected occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- issue severity
- vendor availability
- photo/video evidence
- habitability impact

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to repeated access-code confusion detected.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Property knowledge
- Guest profile memory
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 467. Repeated parking question across properties

### Stage
Internal operations

### Trigger
Repeated parking question across properties occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- parking fact
- space availability
- permit requirement
- issue severity

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Classify severity, request evidence if not already provided, route to vendor/ops, and follow up until guest confirms resolution.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- property knowledge
- last verified fact

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
No

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Property-specific guest-facing fact or recurring confusion related to repeated parking question across properties.

### Example Learned Pattern
Parking questions for this property should use the verified permit/space instruction and avoid promising availability when not reserved.

### Memory Type
- Property knowledge
- Guest profile memory
- Task workflow memory

### What Not to Learn
Do not apply one property's amenity rule to other properties unless the user scopes it explicitly.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 468. Repeated owner approval delay

### Stage
Internal operations

### Trigger
Repeated owner approval delay occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status
- owner preference
- owner approval SLA
- property scope

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified. Use owner preference memory and produce owner-safe operational summary; avoid exposing guest-sensitive data unnecessarily.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority
- owner preference memory

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Owner-specific preference or approval behavior for repeated owner approval delay.

### Example Learned Pattern
Owner-specific rules override learned PM behavior for that property group when explicitly confirmed.

### Memory Type
- Guest profile memory
- Owner preference memory
- Task workflow memory

### What Not to Learn
Do not apply one owner's preference to the whole portfolio.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.

## 469. Night shift handoff missing context

### Stage
Internal operations

### Trigger
Night shift handoff missing context occurs via OTA inbox, direct booking channel, WhatsApp, email, phone note, PMS event, team note, or internal task update.

### Signals to Inspect
- actor
- workflow
- SLA timer
- affected reservations
- affected properties
- data conflict
- approval threshold
- integration status

### Risk Level
Medium

### AI Default Behavior
Handle as supervised automation: check required data first, draft or propose the next action, and only auto-send if policy, price, and availability are verified.

### Required Data Checks
- task state
- vendor SLA
- cleaning schedule
- PMS/OTA consistency
- integration health
- owner preferences
- approval authority

### Should AI Auto-Reply?
Conditional

### Should AI Escalate to PM?
Conditional

### Should AI Create Task?
Yes

### Should AI Learn Pattern?
Yes

### Pattern to Learn
Repeated PM handling pattern for night shift handoff missing context, only if similar decisions recur across enough comparable cases.

### Example Learned Pattern
If the PM makes the same decision in 3+ comparable cases, propose a scoped rule rather than global automation.

### Memory Type
- Task workflow memory
- Missing-info registry

### What Not to Learn
Do not convert one-off operational context into a global rule; keep the pattern scoped to similar facts, property, channel, and time.

### Future Behavior Impact
Next time, Cendra should pre-check the same data, propose the likely PM-approved action, and move toward semi-auto only after repeated low-risk approvals.


---

## 10. Engineering Notes for Classifiers

Recommended top-level classifier dimensions:

- journey_stage
- scenario_family
- risk_level
- money_impact
- access_impact
- safety_impact
- policy_impact
- source_of_truth_required
- required_memory_types
- required_tool_checks
- escalation_target
- task_type
- automation_mode
- reversibility
- pattern_learning_eligibility
- pattern_scope
- confidence_target

Recommended scenario families:

- inquiry_risk
- booking_verification
- prearrival_access
- housekeeping_readiness
- checkin_access_failure
- checkin_quality_issue
- maintenance_issue
- safety_security
- amenity_question
- house_rule_enforcement
- upsell_pricing
- refund_compensation
- damage_deposit
- checkout_operations
- review_recovery
- owner_workflow
- vendor_workflow
- integration_health
- knowledge_gap
- channel_policy

---

## 11. Product Notes for Approval UX

High-risk scenarios must create Decision Cards with:

- situation summary
- evidence pack
- required checks completed
- proposed action
- risk reason
- reversibility
- approval owner
- safer alternative
- “what happens if declined”
- audit link

Sensitive workflows that default to PM approval:

- damage
- cancellation exception
- refund
- compensation
- guest charges
- access code timing
- price negotiation
- platform/off-channel payment
- legal/safety/privacy
- serious maintenance affecting habitability
- owner-disputed decision
- finance/payment failures

---

## 12. QA Notes

For each scenario, QA should create test cases for:

1. verified data available
2. missing property knowledge
3. PMS/channel conflict
4. integration degraded
5. guest provides evidence
6. guest provides no evidence
7. PM approves
8. PM rejects
9. PM edits reply
10. repeated similar cases create PatternRule candidate
11. risky learned rule requires confirmation
12. auto mode demotes after incident

---

## 13. Final Principle

Cendra should not learn everything.

It should learn:
- confirmed facts
- repeated PM behavior
- scoped operational rules
- vendor reliability
- guest preferences and risk signals
- owner preferences
- recurring property issues
- missing knowledge patterns

It should avoid learning:
- one-off emotional messages
- unverified guest accusations
- emergency-specific details
- money-impacting decisions without approval
- unsafe shortcuts around channel policy
- property-specific rules as global defaults
- guest risk from a single unsupported event

The correct default is:

> If the action is low-risk, reversible, and verified, automate.  
> If it affects money, access, safety, policy, or reputation, prepare the evidence and ask.  
> If a PM repeats the same safe decision enough times, suggest a scoped rule.  
> If the PM explicitly confirms an SOP, preserve it and apply it with risk gates.
