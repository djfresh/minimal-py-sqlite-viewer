# Collaboration Guidelines and Codebase Quality Standards

To ensure smooth collaboration and maintain the high quality of our codebase, please adhere to the following guidelines:

## Branching Strategy

*   **`premain`**:
    *   Always push your changes to the `premain` branch initially.
    *   This safeguards the `main` branch from unintentional disruptions.
    *   All tests will be performed on the `premain` branch.
    *   Changes will only be merged into `main` after several hours or days of rigorous testing.
*   **`experimental`**:
    *   For large or potentially disruptive changes, use the `experimental` branch.
    *   This allows for thorough discussion and review before considering a merge into `main`.
