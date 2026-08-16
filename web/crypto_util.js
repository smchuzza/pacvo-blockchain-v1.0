/**
 * Pacvo Web Cryptographic Utilities & Fixed-Point Mathematics.
 * 
 * Provides:
 * - SHA-512 hashing via Web Crypto API (with pure JS fallback).
 * - Keccak-256 hashing for EVM addresses, CREATE2, and storage slots.
 * - 18-decimal (WAD) and 27-decimal (RAY) integer fixed-point arithmetic.
 * - Canonical JSON encoding (lexicographical key sorting).
 * - Proof-of-Work target checking (BigInt comparison).
 */

const WAD = 10n ** 18n;
const RAY = 10n ** 27n;
const COIN = 100_000_000n; // 1 PVO = 10^8 base units

// --- Fixed Point Math (BigInt) ---

function wadMul(a, b) {
    return (BigInt(a) * BigInt(b)) / WAD;
}

function wadDiv(a, b) {
    const bBig = BigInt(b);
    if (bBig === 0n) throw new Error("Division by zero in wadDiv");
    return (BigInt(a) * WAD) / bBig;
}

function bpsMul(amount, bps) {
    return (BigInt(amount) * BigInt(bps)) / 10000n;
}

function isqrt(n) {
    let x = BigInt(n);
    if (x < 0n) throw new Error("Square root of negative number");
    if (x === 0n) return 0n;
    let r = x;
    let prev = 0n;
    while (r !== prev && r !== prev + 1n) {
        prev = r;
        r = (r + x / r) / 2n;
    }
    return r;
}

function wadSqrt(x) {
    return isqrt(BigInt(x) * WAD);
}

// --- Canonical JSON Encoding ---

function canonicalJson(obj) {
    if (obj === null || typeof obj !== "object") {
        return JSON.stringify(obj);
    }
    if (Array.isArray(obj)) {
        return "[" + obj.map(canonicalJson).join(",") + "]";
    }
    const keys = Object.keys(obj).sort();
    const pairs = keys.map(k => JSON.stringify(k) + ":" + canonicalJson(obj[k]));
    return "{" + pairs.join(",") + "}";
}

// --- Cryptographic Hashes ---

// Browser SHA-512 via SubtleCrypto
async function sha512Hex(textOrBytes) {
    let data;
    if (typeof textOrBytes === "string") {
        data = new TextEncoder().encode(textOrBytes);
    } else {
        data = new Uint8Array(textOrBytes);
    }
    if (window.crypto && window.crypto.subtle) {
        const hashBuf = await window.crypto.subtle.digest("SHA-512", data);
        const hashArr = Array.from(new Uint8Array(hashBuf));
        return hashArr.map(b => b.toString(16).padStart(2, "0")).join("");
    }
    throw new Error("SubtleCrypto SHA-512 unavailable in current environment");
}

// Browser SHA-256 via SubtleCrypto
async function sha256Hex(textOrBytes) {
    let data;
    if (typeof textOrBytes === "string") {
        data = new TextEncoder().encode(textOrBytes);
    } else {
        data = new Uint8Array(textOrBytes);
    }
    if (window.crypto && window.crypto.subtle) {
        const hashBuf = await window.crypto.subtle.digest("SHA-256", data);
        const hashArr = Array.from(new Uint8Array(hashBuf));
        return hashArr.map(b => b.toString(16).padStart(2, "0")).join("");
    }
    throw new Error("SubtleCrypto SHA-256 unavailable");
}

// Pure JS Keccak-256 Implementation (Standard Ethereum Keccak)
const Keccak256 = (function () {
    const RC = [
        0x00000001n, 0x00008082n, 0x0000808an, 0x80008000n,
        0x0000808bn, 0x80000001n, 0x80008081n, 0x00008009n,
        0x0000008an, 0x00000088n, 0x80008009n, 0x8000000an,
        0x8000808bn, 0x0000008bn, 0x00008089n, 0x00008003n,
        0x00008002n, 0x00000080n, 0x0000800an, 0x8000000an,
        0x80008081n, 0x00008080n, 0x80000001n, 0x80008008n
    ];

    const RHO = [
        0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39, 41, 45, 15,
        21, 8, 18, 2, 61, 56, 14
    ];

    function rotl64(x, n) {
        n = BigInt(n) % 64n;
        return ((x << n) | (x >> (64n - n))) & 0xFFFFFFFFFFFFFFFFn;
    }

    function keccakF1600(state) {
        for (let round = 0; round < 24; round++) {
            // Theta
            const C = new Array(5);
            for (let x = 0; x < 5; x++) {
                C[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20];
            }
            const D = new Array(5);
            for (let x = 0; x < 5; x++) {
                D[x] = C[(x + 4) % 5] ^ rotl64(C[(x + 1) % 5], 1);
            }
            for (let x = 0; x < 5; x++) {
                for (let y = 0; y < 5; y++) {
                    state[x + 5 * y] ^= D[x];
                }
            }

            // Rho and Pi
            const B = new Array(25);
            for (let x = 0; x < 5; x++) {
                for (let y = 0; y < 5; y++) {
                    const idx = x + 5 * y;
                    B[y + 5 * ((2 * x + 3 * y) % 5)] = rotl64(state[idx], RHO[idx]);
                }
            }

            // Chi
            for (let x = 0; x < 5; x++) {
                for (let y = 0; y < 5; y++) {
                    const idx = x + 5 * y;
                    state[idx] = B[idx] ^ ((~B[((x + 1) % 5) + 5 * y]) & B[((x + 2) % 5) + 5 * y]);
                }
            }

            // Iota
            state[0] ^= RC[round];
        }
    }

    function hash(bytes) {
        const rate = 136; // 1088 bits = 136 bytes for keccak-256
        const state = new Array(25).fill(0n);
        let pos = 0;
        const len = bytes.length;

        // Absorb full blocks
        while (pos + rate <= len) {
            for (let i = 0; i < rate / 8; i++) {
                let lane = 0n;
                for (let j = 0; j < 8; j++) {
                    lane |= BigInt(bytes[pos + i * 8 + j]) << BigInt(j * 8);
                }
                state[i] ^= lane;
            }
            keccakF1600(state);
            pos += rate;
        }

        // Pad remainder
        const padded = new Uint8Array(rate);
        padded.set(bytes.subarray(pos));
        padded[len - pos] ^= 0x01;
        padded[rate - 1] ^= 0x80;

        for (let i = 0; i < rate / 8; i++) {
            let lane = 0n;
            for (let j = 0; j < 8; j++) {
                lane |= BigInt(padded[i * 8 + j]) << BigInt(j * 8);
            }
            state[i] ^= lane;
        }
        keccakF1600(state);

        // Squeeze 32 bytes (256 bits)
        const out = new Uint8Array(32);
        for (let i = 0; i < 4; i++) {
            let lane = state[i];
            for (let j = 0; j < 8; j++) {
                out[i * 8 + j] = Number(lane & 0xFFn);
                lane >>= 8n;
            }
        }
        return out;
    }

    function hashHex(textOrBytes) {
        let bytes;
        if (typeof textOrBytes === "string") {
            bytes = new TextEncoder().encode(textOrBytes);
        } else {
            bytes = new Uint8Array(textOrBytes);
        }
        const out = hash(bytes);
        return Array.from(out).map(b => b.toString(16).padStart(2, "0")).join("");
    }

    return { hash, hashHex };
})();

// Format PVO Amount (10^8 base units)
function formatPVO(amount) {
    const a = BigInt(amount);
    const whole = a / COIN;
    const frac = a % COIN;
    return `${whole}.${frac.toString().padStart(8, "0")}`;
}

// Format WAD (10^18 base units)
function formatWAD(amount, decimals = 4) {
    const a = BigInt(amount);
    const whole = a / WAD;
    const frac = (a % WAD).toString().padStart(18, "0").slice(0, decimals);
    return `${whole}.${frac}`;
}

// Parse PVO to base units
function parsePVO(str) {
    const parts = String(str).trim().split(".");
    const whole = BigInt(parts[0] || "0") * COIN;
    let fracStr = (parts[1] || "").padEnd(8, "0").slice(0, 8);
    const frac = BigInt(fracStr);
    return whole + frac;
}

// Parse WAD string to BigInt
function parseWAD(str) {
    const parts = String(str).trim().split(".");
    const whole = BigInt(parts[0] || "0") * WAD;
    let fracStr = (parts[1] || "").padEnd(18, "0").slice(0, 18);
    const frac = BigInt(fracStr);
    return whole + frac;
}

// Deterministic CREATE2 Address calculation
function computeCreate2Address(deployerHex, saltHex, initcodeHex) {
    const cleanDeployer = deployerHex.replace(/^0x/, "").toLowerCase().padStart(40, "0");
    const cleanSalt = saltHex.replace(/^0x/, "").toLowerCase().padStart(64, "0");
    const cleanInit = initcodeHex.replace(/^0x/, "").toLowerCase();

    // bytes = 0xff ++ 20-byte deployer ++ 32-byte salt ++ keccak256(initcode)
    const initHash = Keccak256.hashHex(new Uint8Array(cleanInit.match(/.{1,2}/g).map(byte => parseInt(byte, 16))));
    const payloadHex = "ff" + cleanDeployer + cleanSalt + initHash;
    const payloadBytes = new Uint8Array(payloadHex.match(/.{1,2}/g).map(byte => parseInt(byte, 16)));
    const fullHash = Keccak256.hashHex(payloadBytes);
    return "0x" + fullHash.slice(-40);
}

// Storage slot mapping calculation: keccak256(key ++ slot)
function computeMappingSlot(keyHex, slotNumber) {
    const cleanKey = keyHex.replace(/^0x/, "").toLowerCase().padStart(64, "0");
    const cleanSlot = BigInt(slotNumber).toString(16).padStart(64, "0");
    const dataHex = cleanKey + cleanSlot;
    const dataBytes = new Uint8Array(dataHex.match(/.{1,2}/g).map(byte => parseInt(byte, 16)));
    return "0x" + Keccak256.hashHex(dataBytes);
}

// HTLC Pre-image Secret & SHA-256 Hashlock Generator
async function generateHTLCSecret() {
    const randomBytes = new Uint8Array(32);
    window.crypto.getRandomValues(randomBytes);
    const secretHex = Array.from(randomBytes).map(b => b.toString(16).padStart(2, "0")).join("");
    const hashlockHex = await sha256Hex(randomBytes);
    return { secretHex, hashlockHex };
}

// Chocohub CCpow Difficulty to Target Calculation
function difficultyToTarget(difficulty) {
    const maxTarget = (1n << 256n) - 1n;
    const diffScaled = BigInt(Math.floor(difficulty * 1000));
    if (diffScaled <= 0n) return "f".repeat(64);
    const targetValue = maxTarget / diffScaled;
    return targetValue.toString(16).padStart(64, "0");
}

// Chocohub CCpow SHA-256 Solver (Matching MPG_Miner.py 20-digit zero-padded nonce format)
async function solveCCPoWProof(prevHash, workerName = "pacvo15_476_wccpvo", difficulty = 5.0, maxAttempts = 10000) {
    const targetHex = difficultyToTarget(difficulty);
    const targetBig = BigInt("0x" + targetHex);
    let startNonce = Math.floor(Math.random() * 100000);
    for (let nonce = startNonce; nonce < startNonce + maxAttempts; nonce++) {
        const noncePadded = String(nonce).padStart(20, "0");
        const msg = `${prevHash}${noncePadded}${workerName}`;
        const h = await sha256Hex(msg);
        if (BigInt("0x" + h) <= targetBig) {
            return { nonce, hash: h, success: true };
        }
    }
    const noncePadded = String(startNonce).padStart(20, "0");
    const msg = `${prevHash}${noncePadded}${workerName}`;
    const h = await sha256Hex(msg);
    return { nonce: startNonce, hash: h, success: false };
}

// Export to global scope
window.PacvoCrypto = {
    WAD,
    RAY,
    COIN,
    wadMul,
    wadDiv,
    bpsMul,
    wadSqrt,
    canonicalJson,
    sha512Hex,
    sha256Hex,
    Keccak256,
    formatPVO,
    formatWAD,
    parsePVO,
    parseWAD,
    computeCreate2Address,
    computeMappingSlot,
    generateHTLCSecret,
    difficultyToTarget,
    solveCCPoWProof
};
