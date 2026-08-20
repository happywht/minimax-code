/**
 * Spinner — loading indicator.
 */
import { strings } from "./strings";

export interface SpinnerProps {
  size?: number;
  className?: string;
}

export function Spinner({ size = 16, className = "" }: SpinnerProps): JSX.Element {
  return (
    <span
      role="status"
      aria-label={strings.a11y.loading}
      style={{ width: size, height: size }}
      className={
        "inline-block animate-spin rounded-full border-2 border-line border-t-accent " +
        className
      }
    />
  );
}
