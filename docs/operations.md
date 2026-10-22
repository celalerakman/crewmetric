# Site operation guide

## Initial configuration

Create an empty database, two administrator accounts and at least one supervisor. Create projects before work packages, and crews before personnel. Choose a workspace currency during initialization. A person’s hourly rate is the labor cost input used for that person’s activity hours; enter a fully loaded rate if that is the intended costing convention.

Use a different work package when the production unit, zone or scope is materially different. Plaster area, conduit length, reinforcement tonnage and formwork area require different package baselines. For engineering and office work, choose indirect rather than entering artificial production quantities.

## Recording a day

Measure the crew’s incremental completed output for that date, not cumulative project output. Include rework hours in actual attendance; record only accepted incremental production in the quantity field. Explain delays or unusual work conditions in notes. A zero-output direct-work day is valid and exposes hours spent without earned production.

Consolidate multiple crews working on the same package/date into one sheet. Where more than one hundred people contribute, split the work breakdown into distinct measurable zones/packages before recording work. Do not duplicate the same output across packages to work around the attendance limit.

The initial draft captures cost rates. If a rate was entered incorrectly, correct the personnel record and create a replacement draft; adjusting attendance hours alone intentionally keeps its original rate snapshot.

## Reviewing a sheet

Compare personnel, actual durations, overtime and measured output with the site records. A supervisor can submit only their own draft. An administrator cannot approve their own sheet and should use a second administrator for their own entries. Return a submitted sheet with a reason when quantity, notes or existing attendance hours need correction. For a missing or wrong personnel line, create a replacement sheet; the original draft remains in history.

Approval adds both labor and production to reports. Voiding removes both and records a reason without destroying the original. An approved replacement may then use the same package/date.

## Reading performance

- Efficiency `1.00×`: actual hours match the hours earned by completed scope.
- Efficiency below `1.00×`: actual hours exceed earned hours.
- Efficiency above `1.00×`: earned scope exceeds actual labor hours at the baseline norm.
- Hours per unit: lower values imply less recorded labor per measured unit for that same package definition.
- Completion-hour forecast: a linear extension of observed actual hours per unit to the original target.

Budget norms, measurements and work-package definitions must be comparable before drawing conclusions across trades. White-collar and administrative totals describe personnel classification, while direct and indirect totals describe work allocation. Neither is a substitute for the other.

## Planning

Set daily allocations separately from attendance. The planned total across projects cannot exceed the person’s configured capacity. The planning page shows remaining unallocated time and approved actual hours for the chosen day. It does not infer vacation, sickness or unapproved absence.

## Backups

Use the backup command while the server is running to capture a transactionally consistent database. Verify the resulting database with the check command. Test restoration by serving a verified copy at a separate database path and port. Store database backups, session keys and generated credentials outside public source control with appropriate access restrictions.
