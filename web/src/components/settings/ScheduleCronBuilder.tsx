import { useState } from "react";
import { Button, Input } from "../../ui";

const weekdays = [
  ["mon", "周一"], ["tue", "周二"], ["wed", "周三"], ["thu", "周四"],
  ["fri", "周五"], ["sat", "周六"], ["sun", "周日"],
] as const;

/** Populate the existing cron field without changing manually entered expressions. */
export function ScheduleCronBuilder({ onApply, timezone }: { onApply: (cron: string) => void; timezone: string }): JSX.Element {
  const [frequency, setFrequency] = useState("daily");
  const [time, setTime] = useState("09:00");
  const [selectedDays, setSelectedDays] = useState<string[]>(["mon"]);
  const [monthDay, setMonthDay] = useState("1");
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  const validTime = !!match && Number(match[1]) < 24 && Number(match[2]) < 60;
  const validMonthDay = /^\d+$/.test(monthDay) && Number(monthDay) >= 1 && Number(monthDay) <= 31;
  const valid = validTime && (frequency !== "weekly" || selectedDays.length > 0)
    && (frequency !== "monthly" || validMonthDay);
  const apply = () => {
    if (!match || !valid) return;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    const cron = frequency === "hourly" ? `${minute} * * * *`
      : frequency === "weekdays" ? `${minute} ${hour} * * mon-fri`
      : frequency === "weekly" ? `${minute} ${hour} * * ${weekdays.filter(([day]) => selectedDays.includes(day)).map(([day]) => day).join(",")}`
      : frequency === "monthly" ? `${minute} ${hour} ${Number(monthDay)} * *`
      : `${minute} ${hour} * * *`;
    onApply(cron);
  };
  return <div className="my-2 space-y-2 text-xs">
    <div className="flex flex-wrap items-end gap-2">
      <label>快捷频率<select className="block rounded border border-line bg-surface-2 p-1" value={frequency} onChange={(event) => setFrequency(event.target.value)}>
        <option value="hourly">每小时</option><option value="daily">每天</option><option value="weekdays">工作日（周一至周五）</option><option value="weekly">每周指定日</option><option value="monthly">每月指定日期</option>
      </select></label>
      {frequency === "monthly" && <label>每月日期<Input type="number" min={1} max={31} step={1} value={monthDay} onChange={(event) => setMonthDay(event.target.value)} /></label>}
      <label>{frequency === "hourly" ? "每小时的分钟（仅使用分钟部分）" : "时间"}<Input type="time" value={time} onChange={(event) => setTime(event.target.value)} /></label>
      <Button type="button" size="sm" variant="secondary" disabled={!valid} onClick={apply}>填入 Cron</Button>
    </div>
    {frequency === "weekly" && <fieldset>
      <legend className="mb-1">选择星期（可多选）</legend>
      <div className="flex flex-wrap gap-3">{weekdays.map(([day, label]) => <label key={day} className="inline-flex items-center gap-1">
        <input type="checkbox" checked={selectedDays.includes(day)} onChange={(event) => { const checked = event.target.checked; setSelectedDays((current) => checked ? [...current, day] : current.filter((value) => value !== day)); }} />{label}
      </label>)}</div>
      {selectedDays.length === 0 && <p role="status" className="text-status-warning">请至少选择一天。</p>}
    </fieldset>}
    {frequency === "monthly" && <p className="text-ink-2">没有所选日期的月份不会运行（例如 31 日在部分月份不存在）。</p>}
    <p className="text-ink-2">按计划时区 {timezone || "（未设置）"}；填入后仍可手动修改。</p>
  </div>;
}
