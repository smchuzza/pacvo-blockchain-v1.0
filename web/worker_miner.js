/**
 * Pacvo Web Worker SHA-512 Hashcash Miner.
 * 
 * Executes non-blocking parallel SHA-512 proof-of-work loops in the background.
 */

// Canonical JSON encoder for Web Worker context
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

// Convert bytes to hex
function toHex(buf) {
    const arr = new Uint8Array(buf);
    let hex = "";
    for (let i = 0; i < arr.length; i++) {
        hex += arr[i].toString(16).padStart(2, "0");
    }
    return hex;
}

let isMining = false;

self.onmessage = async function (e) {
    const { action, header, targetHex, startNonce, batchSize } = e.data;

    if (action === "stop") {
        isMining = false;
        return;
    }

    if (action === "mine") {
        isMining = true;
        const targetBig = BigInt("0x" + targetHex);
        let nonce = BigInt(startNonce || 0);
        const batch = BigInt(batchSize || 1000);
        const encoder = new TextEncoder();

        while (isMining) {
            let hashesDone = 0;
            const startTime = performance.now();

            for (let i = 0; i < Number(batch) && isMining; i++) {
                header.nonce = Number(nonce);
                const jsonStr = canonicalJson(header);
                const bytes = encoder.encode(jsonStr);

                const hashBuf = await crypto.subtle.digest("SHA-512", bytes);
                const hashHex = toHex(hashBuf);
                const hashBig = BigInt("0x" + hashHex);

                hashesDone++;

                if (hashBig <= targetBig) {
                    // Solution found!
                    self.postMessage({
                        type: "FOUND",
                        nonce: Number(nonce),
                        hashHex: hashHex,
                        header: header,
                        hashesScanned: hashesDone,
                    });
                    isMining = false;
                    return;
                }

                nonce++;
            }

            const elapsedSec = (performance.now() - startTime) / 1000.0;
            const hashrate = elapsedSec > 0 ? hashesDone / elapsedSec : 0;

            self.postMessage({
                type: "PROGRESS",
                currentNonce: Number(nonce),
                hashesDone: hashesDone,
                hashrate: Math.round(hashrate),
            });

            // Brief yield to keep worker responsive to termination signals
            await new Promise(r => setTimeout(r, 0));
        }
    }
};
