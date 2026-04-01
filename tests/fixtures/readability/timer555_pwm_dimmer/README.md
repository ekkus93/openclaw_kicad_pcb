# Timer 555 PWM Dimmer Fixture

Canonical Circuit IR fixture for the intended 555 PWM LED dimmer topology used in CODE_REVIEW8 regression tests.

Purpose:
- provide a stable source IR for end-to-end generation tests
- encode the intended timing, control, gate-drive, and low-side load topology
- support semantic regression checks without depending on the legacy side-format input

Acceptance focus:
- pins 2 and 6 share one timing node
- CTRL decouples only to GND
- the gate path includes both a series resistor and a pull-down
- the load connector models low-side switching