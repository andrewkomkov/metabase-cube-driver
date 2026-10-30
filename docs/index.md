# Metabase driver for Cube

A [Metabase](https://github.com/metabase/metabase) driver plugin for
[Cube Core](https://github.com/cube-js/cube). The aim is that Cube measures, dimensions and segments work
natively in Metabase, for people and for AI agents. The main target is Cube on top of ClickHouse.

This is an unofficial community project. It has no connection with Metabase or Cube.

## What works

[Coverage](coverage.md) shows the last result of the conformance suite. A feature has status `pass` only
if a check ran against a live stack and compared the result with a direct SQL query on ClickHouse. CI
generates that page, and no person writes its numbers.

[Compatibility](compatibility.md) shows which Metabase and Cube versions CI tested.

## Guides

- [Installation](install.md)
- [AI agents](ai-agents.md)
- [CI and releases](ci-and-releases.md)
