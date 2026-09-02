// Supabase Project URL / anon key — 둘 다 공개해도 되는 값 (Supabase 설계상
// anon 키는 클라이언트에 노출되도록 만들어짐; 실제 방벽은 Postgres RLS).
// 커밋해도 안전. 프로젝트 생성 후 Settings → API에서 값을 채워 넣는다.
window.ANALYTICS_CONFIG = {
  supabaseUrl: "REPLACE_WITH_SUPABASE_PROJECT_URL",
  supabaseAnonKey: "REPLACE_WITH_SUPABASE_ANON_KEY",
};
