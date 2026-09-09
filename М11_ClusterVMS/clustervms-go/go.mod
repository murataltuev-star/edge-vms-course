module clustervms

go 1.22

// М10's Go reconciler, imported not copied — the same relationship
// clustervms/ has to nodevms/ in Python.
require nodevms v0.0.0

replace nodevms => ../../М10_NodeVMS/nodevms-go
