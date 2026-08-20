"""Pure domain logic: no I/O, no framework, no persistence.

Every rule that decides whether a formal assessment may start, continue, be submitted, be reviewed
or produce a certificate is a function or a frozen record in this package, so the same rule applies
to an HTTP caller and to a host application calling the services directly.
"""
