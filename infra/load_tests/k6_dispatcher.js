/**
 * k6 yük testi örneği (kurulum: https://k6.io/docs/get-started/installation/)
 *
 * Örnek:
 *   k6 run --vus 50 --duration 30s infra/load_tests/k6_dispatcher.js
 *
 * Önce giriş yapıp JWT alın; aşağıdaki TOKEN değişkenini doldurun veya
 * harici script ile ortam değişkeni verin.
 */
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.DISPATCHER_URL || "http://localhost:8080";
const TOKEN = __ENV.JWT_TOKEN || "";

export const options = {
  stages: [
    { duration: "20s", target: 50 },
    { duration: "40s", target: 100 },
    { duration: "20s", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<2000"],
  },
};

export default function () {
  const params = {
    headers: {
      Authorization: TOKEN ? `Bearer ${TOKEN}` : "",
    },
  };
  const res = http.get(`${BASE}/api/telemetry/query/ping`, params);
  check(res, { "status 2xx/5xx gateway": (r) => r.status >= 200 && r.status < 600 });
  sleep(0.05);
}
