/**
 * FollowRunBar — slim notice shown while the user has manually pinned
 * a tab (follow mode off). Offers a one-click way back to
 * auto-following run activity.
 */
import { Button } from "../../ui";
import { strings } from "../../ui/strings";

export interface FollowRunBarProps {
  testId: string;
  onResume: () => void;
}

export function FollowRunBar({ testId, onResume }: FollowRunBarProps): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-1.5">
      <span className="truncate text-[11px] text-ink-2">{strings.rightPanel.shell.pinned}</span>
      <Button
        variant="subtle"
        size="sm"
        data-testid={`${testId}-resume-follow`}
        onClick={onResume}
        className="h-6 px-2 text-[11px]"
      >
        {strings.rightPanel.shell.followRun}
      </Button>
    </div>
  );
}
