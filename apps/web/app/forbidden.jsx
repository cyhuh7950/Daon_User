export default function Forbidden() {
  return <main className="notebook-route-state" role="alert">
    <h1>접근 권한이 없습니다.</h1>
    <p>사용자 관리는 시스템 관리자만 이용할 수 있습니다.</p>
    <a href="/notebooks">Notebook으로 돌아가기</a>
  </main>;
}
