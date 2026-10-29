import { useState } from "react";
import { Button, Input } from "../../ui";

/** Populate the existing cron field without changing manually entered expressions. */
export function ScheduleCronBuilder({ onApply, timezone }: { onApply: (cron: string) => void; timezone: string }): JSX.Element {
  const [frequency, setFrequency] = useState("daily");
  const [time, setTime] = useState("09:00");
  const [weekday, setWeekday] = useState("mon");
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  const validTime = !!match && Number(match[1]) < 24 && Number(match[2]) < 60;
  const apply = () => {
    if (!match || !validTime) return;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    const cron = frequency === "hourly" ? `${minute} * * * *`
      : frequency === "weekdays" ? `${minute} ${hour} * * mon-fri`
      : frequency === "weekly" ? `${minute} ${hour} * * ${weekday}`
      : `${minute} ${hour} * * *`;
    onApply(cron);
  };
  return <div className="my-2 flex flex-wrap items-end gap-2 text-xs">
    <label>快捷频率<select className="block rounded border border-line bg-surface-2 p-1" value={frequency} onChange={(event) => setFrequency(event.target.value)}>
      <option value="hourly">每小时</option><option value="daily">每天</option><option value="weekdays">工作日（周一至周五）</option><option value="weekly">每周</option>
    </select></label>
    {frequency === "weekly" && <label>星期<select className="block rounded border border-line bg-surface-2 p-1" value={weekday} onChange={(event) => setWeekday(event.target.value)}>
      <option value="mon">周一</option><option value="tue">周二</option><option value="wed">周三</option><option value="thu">周四</option><option value="fri">周五</option><option value="sat">周六</option><option value="sun">周日</option>
    </select></label>}
    <label>{frequency === "hourly" ? "每小时的分钟（仅使用分钟部分）" : "时间"}<Input type="time" value={time} onChange={(event) => setTime(event.target.value)} /></label>
    <Button type="button" size="sm" variant="secondary" disabled={!validTime} onClick={apply}>填入 Cron</Button>
    <span className="text-ink-2">按计划时区 {timezone || "（未设置）"}；填入后仍可手动修改。</span>
  </div>;
}
