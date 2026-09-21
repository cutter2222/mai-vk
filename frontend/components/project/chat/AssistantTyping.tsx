import classes from "./AssistantTyping.module.css";

export function AssistantTyping() {
  return (
    <div className={classes.indicator} role="status" data-testid="assistant-typing">
      <span className={classes.dots} aria-hidden="true"><i /><i /><i /></span>
      <span>Ассистент печатает…</span>
    </div>
  );
}