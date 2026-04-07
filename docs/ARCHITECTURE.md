# Metasphere Architecture

## Core Concepts

### Agent Lifecycle
```
spawn → active → working → [blocked] → complete → sunset
                    ↓
                  failed
```

### Directory Scoping
Agents are tied to specific levels in the project tree:

```
project/
├── .metasphere/              # Project-level agent home
│   ├── agents/@architect/    # Project-wide agents
│   ├── traces/               # Command output traces
│   └── reviews/              # Review agent comments
├── src/
│   ├── .metasphere/          # src-level scope
│   │   ├── agents/@reviewer/ # Code review agent
│   │   └── traces/
│   └── components/
│       └── .metasphere/      # Component-level
└── tests/
    └── .metasphere/
        └── agents/@tester/   # Test-focused agent
```

## Trace System

### Trace Capture
All command outputs are captured to trace files:

```
~/.metasphere/traces/
├── YYYY-MM-DD/
│   ├── HH-MM-SS-agent-command.log     # Individual trace
│   └── HH-MM-SS-agent-command.json    # Structured metadata
└── index.jsonl                         # Searchable index
```

### Trace Format (JSON)
```json
{
  "id": "trace-1234567890",
  "timestamp": "2026-04-06T18:00:00Z",
  "agent": "@implementer",
  "scope": "/path/to/project/src",
  "command": "npm test",
  "exit_code": 1,
  "duration_ms": 5230,
  "stdout_file": "traces/2026-04-06/18-00-00-implementer-npm-test.stdout",
  "stderr_file": "traces/2026-04-06/18-00-00-implementer-npm-test.stderr",
  "error_detected": true,
  "error_type": "test_failure",
  "error_summary": "3 tests failed in auth.test.ts"
}
```

### Error Detection
Traces are analyzed for:
- Non-zero exit codes
- Known error patterns (compilation errors, test failures, etc.)
- Warnings and deprecations
- Security issues (leaked secrets, vulnerable deps)

## Review Agent System

### Scope Hierarchy
Review agents watch their scope and all children:

```
@project-reviewer (scope: /)
  └── sees all traces in project

@src-reviewer (scope: /src)
  └── sees traces in /src and subdirectories

@component-reviewer (scope: /src/components)
  └── sees only component traces
```

### Review Triggers
1. **On Error**: Immediate review when trace has error
2. **On Commit**: Review changed files before commit
3. **Periodic**: Scheduled review of recent traces
4. **On Request**: Manual review trigger

### Review Output
Reviews are stored in the scope's .metasphere/reviews/:

```
.metasphere/reviews/
├── pending/           # Reviews awaiting action
├── resolved/          # Addressed reviews
└── index.jsonl        # All reviews
```

### Review Format
```json
{
  "id": "review-1234",
  "timestamp": "2026-04-06T18:05:00Z",
  "reviewer": "@src-reviewer",
  "trace_id": "trace-1234567890",
  "scope": "/src",
  "severity": "error",
  "category": "test_failure",
  "summary": "Auth tests failing after login refactor",
  "details": "The test expects old token format...",
  "suggestions": [
    "Update test to use new JWT format",
    "Add migration for existing tokens"
  ],
  "related_files": ["src/auth/login.ts", "tests/auth.test.ts"],
  "status": "pending"
}
```

## Git Integration

### Commit Hooks
```
pre-commit:
  1. Run linters (scope-specific)
  2. Check for pending !urgent reviews
  3. Validate task references

post-commit:
  1. Index commit to CAM
  2. Update task progress
  3. Notify relevant agents
```

### Commit Cadence Tracking
```json
{
  "agent": "@implementer",
  "period": "2026-04-06",
  "commits": 5,
  "files_changed": 23,
  "lines_added": 450,
  "lines_removed": 120,
  "tasks_progressed": ["TASK-123", "TASK-124"],
  "reviews_addressed": 2
}
```

### Branch Strategy
- Main agents work on feature branches
- Sub-agents can create sub-branches
- Automatic PR creation when task complete

## Parallelization Model

### Branching (Fan-out)
When to spawn sub-agents:
1. **Independent subtasks**: Can be worked in parallel
2. **Expertise needed**: Task requires specialist
3. **Scope isolation**: Different directories
4. **Blocking dependency**: While waiting, spawn alternate work

```
@orchestrator
  ├── @frontend (scope: /src/ui)
  │   └── working: button component
  ├── @backend (scope: /src/api)
  │   └── working: auth endpoint
  └── @tester (scope: /tests)
      └── blocked: waiting for implementations
```

### Merging (Fan-in)
When to consolidate:
1. **Subtasks complete**: All children done
2. **Integration point**: Need to combine work
3. **Review needed**: Human checkpoint
4. **Conflict detected**: Overlapping changes

### Thinking Phases
```
EXPLORE → PLAN → IMPLEMENT → REVIEW → INTEGRATE
   ↑                              |
   └──────────────────────────────┘
```

- **EXPLORE**: Multiple agents search/research in parallel
- **PLAN**: Consolidate findings, single agent creates plan
- **IMPLEMENT**: Fan out to specialists
- **REVIEW**: Review agents check work
- **INTEGRATE**: Merge, test, commit

## Personality Persistence

### SOUL.md Structure
```markdown
# @agent-name Soul

## Identity
Core purpose and self-description.

## Values
What this agent prioritizes.

## Expertise
Domain knowledge and skills.

## Learned Behaviors
Patterns discovered through experience.
(Updated after each incarnation)

## Communication Style
How to interact with humans and agents.

## Known Pitfalls
Things to avoid based on past failures.
```

### Personality Transfer
When spawning from template:
1. Copy SOUL.md as base
2. Update scope/task sections
3. Inherit learned behaviors
4. Preserve values and identity

### Learning Accumulation
After each sunset:
1. Add learnings to SOUL.md "Learned Behaviors"
2. Update "Known Pitfalls" if failures occurred
3. CAM indexes the learnings for future retrieval
