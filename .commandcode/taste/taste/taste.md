# Taste
- Communicates in Chinese (Simplified) and expects responses in Chinese. Confidence: 0.9
- In git/remote workflows, prefers to run credentialed git operations (e.g., `git push`) himself using locally stored credentials, delegating only browser-side steps (e.g., creating a remote repo on a web UI) to the agent. Confidence: 0.7
- When waiting for the agent to finish a preparation step before he executes the next step himself, prefers a brief, explicit readiness signal (e.g., "好了") over a lengthy report. Confidence: 0.6
- Considers command-line git operations acceptable and available for pushing/syncing (keeps git and GitHub CLI installed locally), and is open to the push itself being done via CLI rather than requiring additional browser-side steps. Confidence: 0.5
- Prefers Microsoft Edge as the browser for browser-automation steps (explicitly directed to use Edge when the agent was probing for Chrome). Confidence: 0.7
- When a remote repository for the project already exists, prefers the agent to detect that and reuse/sync it rather than create a duplicate (corrected the agent's repo-creation plan with "已经有这个仓库名了不需要新建"). Confidence: 0.4
- Gives extremely terse, command-style git instructions (e.g., "切换到<绝对路径> git提交这个项目") and expects the agent to autonomously run the whole workflow in that directory — check status/diffs, stage all changes, craft a commit message in the repo's existing style, commit, and push to the project's established remotes — without pausing for per-step confirmation. Confidence: 0.5
