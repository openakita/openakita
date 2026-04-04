import { memo, useEffect, useState } from "react";
import { getNextSpinnerTip } from "../utils/chatHelpers";

export const SpinnerTipDisplay = memo(function SpinnerTipDisplay() {
  const [tip, setTip] = useState(() => getNextSpinnerTip());
  useEffect(() => {
    const iv = setInterval(() => setTip(getNextSpinnerTip()), 8000);
    return () => clearInterval(iv);
  }, []);
  return <div style={{ fontSize: 11, opacity: 0.5, marginTop: 6, transition: "opacity 0.3s" }}>{tip}</div>;
});
