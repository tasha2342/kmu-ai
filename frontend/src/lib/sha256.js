/**
 * SHA-256 순수 JS 구현입니다. PKCE의 `code_challenge` 계산에만 씁니다.
 *
 * ## 왜 필요한가
 * `crypto.subtle`은 **보안 컨텍스트(HTTPS 또는 localhost)에서만** 노출됩니다.
 * 그래서 `http://192.168.0.146:3002`처럼 평문 HTTP + IP로 접속하면
 * `crypto.subtle`이 undefined가 되어 `Cannot read properties of undefined (reading 'digest')`로 터집니다.
 * (`crypto.getRandomValues`는 보안 컨텍스트가 아니어도 쓸 수 있어 난수 생성은 문제가 없습니다.)
 *
 * 이 구현은 `crypto.subtle`이 없을 때의 대체 경로일 뿐이고, 결과 해시는 완전히 동일합니다.
 * 다만 **평문 HTTP로 접속하면 토큰이 그대로 네트워크에 노출된다는 사실은 변하지 않습니다.**
 * 운영에서는 HTTPS로 서비스하거나 localhost 포트포워딩으로 접속해야 합니다.
 */

/** SHA-256 라운드 상수 (첫 64개 소수의 세제곱근 소수부) */
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

const rotr = (x, n) => (x >>> n) | (x << (32 - n))

/**
 * @param {Uint8Array} bytes 해시할 바이트열
 * @returns {Uint8Array} 32바이트 다이제스트
 */
export function sha256(bytes) {
  const length = bytes.length
  // 패딩: 0x80 한 바이트 + 0x00들 + 길이(비트, 빅엔디언 64비트)로 64바이트 배수를 맞춥니다.
  const padded = new Uint8Array(Math.ceil((length + 9) / 64) * 64)
  padded.set(bytes)
  padded[length] = 0x80

  const view = new DataView(padded.buffer)
  const bitLength = length * 8
  view.setUint32(padded.length - 8, Math.floor(bitLength / 2 ** 32))
  view.setUint32(padded.length - 4, bitLength >>> 0)

  const h = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ])
  const w = new Uint32Array(64)

  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i += 1) w[i] = view.getUint32(offset + i * 4)
    for (let i = 16; i < 64; i += 1) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0
    }

    let [a, b, c, d, e, f, g, hh] = h
    for (let i = 0; i < 64; i += 1) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
      const ch = (e & f) ^ (~e & g)
      const t1 = (hh + s1 + ch + K[i] + w[i]) >>> 0
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (s0 + maj) >>> 0

      hh = g
      g = f
      f = e
      e = (d + t1) >>> 0
      d = c
      c = b
      b = a
      a = (t1 + t2) >>> 0
    }

    const round = [a, b, c, d, e, f, g, hh]
    for (let i = 0; i < 8; i += 1) h[i] = (h[i] + round[i]) >>> 0
  }

  const out = new Uint8Array(32)
  const outView = new DataView(out.buffer)
  for (let i = 0; i < 8; i += 1) outView.setUint32(i * 4, h[i])
  return out
}
