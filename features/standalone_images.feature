Feature: Standalone image verification
  Image behavior is verified in this repository without running mvm.

  Scenario: Kernels cannot bind a guest network device
    Given the mvm-images source tree
    When I inspect the kernel device contract
    Then the contract passes

  Scenario: The browser image has no packet-networking fallback
    Given the mvm-images source tree
    When I inspect the QEMU-Wasm device contract
    Then the contract passes

  Scenario: Direct VMM plans expose vsock but no network interface
    Given the mvm-images source tree
    When I inspect the standalone E2E plans
    Then the contract passes

  Scenario: The rootless tenant is generic and remains vsock-only
    Given the mvm-images source tree
    When I inspect the rootless tenant contract
    Then the contract passes

  Scenario: Test entry points do not launch mvm
    Given the mvm-images source tree
    When I inspect the standalone test entry points
    Then the contract passes
