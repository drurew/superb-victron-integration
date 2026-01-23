# Changelog

All notable changes to the SuperB Victron Integration project will be documented in this file.

## [2.0.0] - 2026-01-21

### Changed
- **Major Architecture Change**: Shifted from remote-installation to local on-device installation via Package Manager.
- **Setup Process**: Deprecated `install_to_cerbo.sh` in favor of standard `setup` script.

### Added
- **SetupHelper Integration**: Native support for installing via Victron Package Manager.
- **CI/CD**: Automated release building via GitHub Actions.
- **Packaging**: Self-contained `create_package.sh` script.
- **Documentation**: New installation instructions for offline/USB and online methods.

## [1.1.0] - 2026-01-10

### Added
- Multi-battery monitoring support.
- Improved filtering for CANopen messages.

## [1.0.0] - 2025-12-25

### Added
- Initial release for SuperB Epsilon V2.
- Basic CANopen SDO reading and D-Bus publishing.
