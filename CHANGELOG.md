# Changelog

All notable changes to the SuperB Victron Integration project.

## [2.1.0] - 2026-05-18

### Added
- **C driver** (`src/victron-bms.c`): Native driver linking against libc only.
  Uses raw SocketCAN and D-Bus wire protocol. 10 KB binary, 1 MB RSS, near-zero
  CPU. Zero runtime dependencies.
- **Dynamic charge limits**: SDO objects 0x5021 and 0x2060 correctly mapped from
  the Be In Charge CANOpen.dll decompilation. Max charge current, max discharge
  current, and requested charge voltage now published per battery.
- **Makefile** for native compilation on Cerbo GX.
- **Installation guide** (`docs/INSTALL.md`) with daemontools service setup.

### Changed
- **Repository restructured**: C driver in `src/`, documentation in `docs/`,
  scripts in `scripts/`.
- **SDO timeout** reduced from 500 ms to 150 ms.
- **Abort blacklisting**: Objects returning SDO abort are skipped on subsequent
  cycles, eliminating repeated timeout delays.

### Fixed
- **Charge limits missing**: Previously read from non-existent object 0x5013
  (interface protocol selector). Corrected to 0x5021 (BatteryBase.GetMaxChargeCurrent
  / GetMaxDischargeCurrent) and 0x2060 (SuperBBase.GetBatteryRequestedChargeVoltage).

## [2.0.0] - 2026-01-21

### Changed
- **Major Architecture Change**: Shifted from remote-installation to local
  on-device installation via Package Manager.
- **Setup Process**: Deprecated `install_to_cerbo.sh` in favor of standard
  `setup` script.

### Added
- **SetupHelper Integration**: Native support for installing via Victron
  Package Manager.
- **CI/CD**: Automated release building via GitHub Actions.
- **Packaging**: Self-contained `create_package.sh` script.
- **Documentation**: New installation instructions for offline/USB and online
  methods.

## [1.1.0] - 2026-01-10

### Added
- Multi-battery monitoring support.
- Improved filtering for CANopen messages.

## [1.0.0] - 2025-12-25

### Added
- Initial release for SuperB Epsilon V2.
- Basic CANopen SDO reading and D-Bus publishing.
