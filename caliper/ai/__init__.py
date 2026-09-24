"""The AI layer: an assistant that changes and checks a sketch through Caliper's own interfaces.

It is a peer of the shell and of scripts (see docs/architecture.md): every change it makes
is a `Command` sent to a bus, and everything it learns comes from `Queries`. It imports
`contracts` and `engine`, never `app` or Qt, and no module here depends on a particular
model; `claude` is one implementation of the `model.Model` protocol.
"""
