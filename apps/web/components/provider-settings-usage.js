export function summarizeConnectionUsage(connections) {
  const activeCount = connections.filter((connection) => connection?.enabled === true).length;
  return {
    activeCount,
    label: activeCount ? `사용 후보 ${activeCount}개` : "사용 후보 없음",
    description: activeCount
      ? "활성화된 연결은 Provider 선택 후보로 사용됩니다."
      : "연결을 활성화하면 Provider 선택 후보가 됩니다.",
  };
}
