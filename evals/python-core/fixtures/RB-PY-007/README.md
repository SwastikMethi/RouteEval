# Transport dispatch
`transports.create_transport(name, endpoint, *, timeout=5)`,
`transports.send(name, endpoint, payload, *, timeout=5)`, and
`transports.preview(name, endpoint, *, timeout=5)` duplicate built-in selection.
Replace their transport-name branches with one shared internal registry used by
all three paths. Remove conditional comparisons or match cases against built-in
names ('memory', 'console', 'file') throughout the package; registry lookup must
choose factories. Keep behavior and exception types unchanged.

Public compatibility imports from `transports`, `transports.client`,
`transports.sender`, `transports.preview`, and `transports.registration` remain
supported. Built-in factories are classes in transports.implementations. They
accept endpoint and keyword timeout unchanged. create_transport returns that
instance; send returns its send(payload); preview returns its describe(). The
factory may be any callable. Forward options on every path without normalization.
Unknown names raise UnknownTransport (a ValueError) with args==(name,).
`register(name, factory)` makes custom factories available through all paths;
collisions with built-ins or prior registrations raise ValueError and leave the
original registration intact. Constructor exceptions propagate unchanged.
Refactoring is the task: existing behavioral tests can already pass. A structural
check requires the duplicated built-in dispatch branches to be removed.
Change only `transports/`; no dependencies or test edits.
Run `python -m pytest -q tests`.
