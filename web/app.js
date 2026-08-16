/**
 * Pacvo Web Interface — Main Application Logic Engine
 * 
 * Orchestrates:
 * - Wallet State & Cryptographic Addressing (Bech32 Native & EVM 0x)
 * - Multi-threaded SHA-512 Hashcash Web Miner
 * - Layer 2 Asset Studio (ERC-20 Tokens & ERC-721 NFTs)
 * - Layer 3 PVO-Fi Economy (4 POL Polygon Reserve, AMM DEX, Lending, Equities, Bridges)
 * - JSON-RPC Gateway & Activity Logging
 */

// Global State
const state = {
    wallet: {
        publicKeyHex: "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00",
        nativeAddress: "pvo17a9e03d4218a56fb0823c14d9e03829104bcefa8102938475610293847561029384756102938475610293847561029384756102938475610293847561029",
        evmAddress: "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
        spendableBalance: 0,
        stakedBalance: 0,
        immatureBalance: 0,
        nonce: 0,
        transactions: []
    },
    blockchain: {
        tipHeight: 0,
        prevHash: "",
        activeTargetHex: "3fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        minedBlocks: [],
        epoch: 0
    },
    nodeMiner: {
        height: 0,
        tip_hash: "",
        target_hex: "",
        cumulative_work: 0,
        mempool_size: 0,
        peer_count: 0,
        miner_address: "",
        miner_spendable: 0,
        miner_staked: 0,
        miner_immature: 0,
        miner_locked_entries: [],
        recent_blocks: []
    },
    l2: {
        tokens: [
            {
                symbol: "PUSD",
                name: "Pacvo Stable USD",
                address: "0x1111111111111111111111111111111111111111",
                totalSupply: "1,000,000",
                myBalance: "10,000",
                type: "Controlled Mint",
                decimals: 18
            },
            {
                symbol: "PVOA",
                name: "Pacvo Asset Token",
                address: "0x2222222222222222222222222222222222222222",
                totalSupply: "500,000",
                myBalance: "50,000",
                type: "Fixed Supply",
                decimals: 18
            },
            {
                symbol: "COLLAT",
                name: "Simulated Collateral Asset",
                address: "0x3333333333333333333333333333333333333333",
                totalSupply: "2,000,000",
                myBalance: "10,000",
                type: "Controlled Mint",
                decimals: 18
            }
        ],
        nftCollections: [
            {
                name: "Pacvo Genesis Artifacts",
                symbol: "PVONFT",
                address: "0x7777777777777777777777777777777777777777",
                owner: "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
                totalMinted: 4
            }
        ],
        nfts: [
            {
                collection: "PVONFT",
                tokenId: 1,
                name: "Genesis Node #001",
                owner: "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
                icon: "💠",
                uri: "ipfs://bafy.../1"
            },
            {
                collection: "PVONFT",
                tokenId: 2,
                name: "SPHINCS+ Master Key",
                owner: "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
                icon: "🛡️",
                uri: "ipfs://bafy.../2"
            },
            {
                collection: "PVONFT",
                tokenId: 3,
                name: "ML-KEM Transport Beacon",
                owner: "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
                icon: "⚡",
                uri: "ipfs://bafy.../3"
            },
            {
                collection: "PVONFT",
                tokenId: 4,
                name: "Polygon Genesis Reserve Key",
                owner: "0xe9D970937ba528245BAeD156aFe036e0Fa565218",
                icon: "💎",
                uri: "ipfs://bafy.../4"
            }
        ]
    },
    l3: {
        reserve: {
            floorPOL: 4.0,
            grossPOL: 4.0,
            encumberedPOL: 0.0,
            availablePOL: 4.0,
            verifiedPOL: 4.0,
            isSolvent: true
        },
        amm: {
            reservePOL: 10000.0,
            reservePVOA: 10000.0,
            invariantK: 100000000.0,
            feeBps: 30
        },
        lending: {
            collatAmount: 10000,
            debtAmount: 5000,
            collatPrice: 1.0,
            debtPrice: 1.0,
            liqThresholdBps: 12000, // 120%
            isLiquidatable: false
        },
        equity: {
            symbol: "EQPOCH",
            totalSupply: 10000,
            userBalance: 5000,
            cumulativePerShare: 0,
            userLastIndex: 0,
            claimableDividend: 0
        },
        bridges: {
            btcLockedSat: 100_000_000, // 1.0 BTC
            btcMintedWad: 998_500_000_000_000_000n, // 0.9985 WAD
            xnoLockedRaw: 100n * 10n ** 30n, // 100 XNO
            xnoMintedWad: 99_900_000_000_000_000_000n, // 99.90 WAD
            ccLockedRaw: 100_000_000_000, // 1000 CC
            ccMintedWad: 999_000_000_000_000_000_000n // 999.0 WAD
        }
    },
    htlc: {
        orders: [],
        escrowPvo: 0.0,
        escrowCc: 0.0,
        totalSwappedPvo: 0.0,
        totalMinedCc: 0.0,
        currentSecret: "",
        currentHashlock: "",
        isMining: false,
        miningInterval: null,
        miningTier: "cpu",
        solvedProofs: 0,
        lastProofHash: "",
        worker: "pacvo15_476_wccpvo",
        authenticated: false,
        server: "configured",
        minerScript: "MPG_Miner.py"
    }
};

// --- Initialization ---

async function fetchCcpowStatus() {
    try {
        const res = await fetch("/api/ccpow/config");
        if (res.ok) {
            const data = await res.json();
            if (data.worker) {
                state.htlc.worker = data.worker;
                const workerLabel = document.getElementById("htlc-worker-label");
                if (workerLabel) workerLabel.innerText = data.worker;
            }
            state.htlc.authenticated = Boolean(data.authenticated);
            state.htlc.server = data.server || "configured";
        }
    } catch (e) {
        // Background status fetch silently fails if offline
    }
}

document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initWallet();
    renderAll();
    fetchNodeMinerStatus();
    fetchCcpowStatus();
    startMinerAutoPoll();
    logActivity("Pacvo Web Interface initialized. Multi-Layer SPHINCS+/EVM/PVO-Fi Engine ready.", "info");
});

// --- Tab Navigation ---

function initNavigation() {
    const tabs = document.querySelectorAll("#main-nav-tabs .tab-btn");
    tabs.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-tab");
            switchToTab(targetId);
        });
    });
}

function switchToTab(tabId) {
    document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));
    document.querySelectorAll("#main-nav-tabs .tab-btn").forEach(b => b.classList.remove("active"));

    const targetPane = document.getElementById(tabId);
    const targetBtn = document.querySelector(`[data-tab="${tabId}"]`);
    if (targetPane) targetPane.classList.add("active");
    if (targetBtn) targetBtn.classList.add("active");
}

function switchL2Subtab(subtab) {
    const secErc20 = document.getElementById("l2-section-erc20");
    const secNft = document.getElementById("l2-section-nft");
    const btnErc20 = document.getElementById("btn-subtab-erc20");
    const btnNft = document.getElementById("btn-subtab-nft");

    if (subtab === "erc20") {
        secErc20.style.display = "flex";
        secNft.style.display = "none";
        btnErc20.className = "btn btn-primary";
        btnNft.className = "btn btn-secondary";
    } else {
        secErc20.style.display = "none";
        secNft.style.display = "flex";
        btnErc20.className = "btn btn-secondary";
        btnNft.className = "btn btn-primary";
    }
}

function switchL3Subtab(subtab) {
    const subtabs = ["amm", "lending", "equity", "basket", "bridge"];
    subtabs.forEach(name => {
        const sec = document.getElementById(`l3-section-${name}`);
        const btn = document.getElementById(`btn-subtab-${name}`);
        if (sec) sec.style.display = (name === subtab) ? "flex" : "none";
        if (btn) btn.className = (name === subtab) ? "btn btn-primary" : "btn btn-secondary";
    });
}

// --- Logging Console ---

function logActivity(message, level = "info") {
    const consoleEl = document.getElementById("system-activity-console");
    if (!consoleEl) return;
    const timeStr = new Date().toLocaleTimeString();
    const entry = document.createElement("div");
    entry.className = "log-entry";
    
    let levelClass = "log-info";
    if (level === "success") levelClass = "log-success";
    if (level === "warning") levelClass = "log-warning";
    if (level === "danger") levelClass = "log-danger";

    entry.innerHTML = `<span class="log-time">[${timeStr}]</span> <span class="${levelClass}">${escapeHtml(message)}</span>`;
    consoleEl.appendChild(entry);
    consoleEl.scrollTop = consoleEl.scrollHeight;
}

function clearSystemLog() {
    const consoleEl = document.getElementById("system-activity-console");
    if (consoleEl) consoleEl.innerHTML = "";
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

// --- JSON-RPC Gateway Client ---

async function callNodeRPC(method, params = {}) {
    try {
        const resp = await fetch("/api/rpc", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                method: method,
                params: params,
                node: "127.0.0.1:9442"
            })
        });
        const data = await resp.json();
        return data;
    } catch (err) {
        console.warn("RPC fetch failed:", err);
        return { status: "error", error: String(err) };
    }
}

// --- Wallet Functions & SPHINCS+ Creation Engine ---

let newlyCreatedWalletData = null;
let loadedWalletFileJson = null;

function initWallet() {
    updateWalletUI();
    syncWalletFromNode();
}

function copyWalletAddress() {
    navigator.clipboard.writeText(state.wallet.nativeAddress);
    logActivity("Copied Native SPHINCS+ Address to clipboard.", "info");
}

function copyEvmAddress() {
    navigator.clipboard.writeText(state.wallet.evmAddress);
    logActivity("Copied EVM 0x Address to clipboard.", "info");
}

function updateWalletUI() {
    const elNative = document.getElementById("wallet-native-address");
    if (elNative) elNative.innerText = state.wallet.nativeAddress;
    const elEvm = document.getElementById("wallet-evm-address");
    if (elEvm) elEvm.innerText = state.wallet.evmAddress;
    const elSpend = document.getElementById("wallet-spendable-bal");
    if (elSpend) elSpend.innerText = `${(state.wallet.spendableBalance / 100_000_000).toFixed(8)} PVO`;
    const elStake = document.getElementById("wallet-staked-bal");
    if (elStake) elStake.innerText = `${(state.wallet.stakedBalance / 100_000_000).toFixed(8)} PVO`;
    const elImmature = document.getElementById("wallet-immature-bal");
    if (elImmature) elImmature.innerText = `${((state.wallet.immatureBalance || 0) / 100_000_000).toFixed(8)} PVO`;
    const elNonce = document.getElementById("wallet-nonce-val");
    if (elNonce) elNonce.innerText = state.wallet.nonce;

    // Dashboard Sync
    const elDashSpend = document.getElementById("dash-spendable-pvo");
    if (elDashSpend) elDashSpend.innerText = (state.wallet.spendableBalance / 100_000_000).toFixed(8);
    const elDashStake = document.getElementById("dash-staked-pvo");
    if (elDashStake) elDashStake.innerText = `${(state.wallet.stakedBalance / 100_000_000).toFixed(8)} PVO`;
}

async function handleCreateWallet(e) {
    e.preventDefault();
    const pass = document.getElementById("new-wallet-passphrase").value;
    const confirm = document.getElementById("new-wallet-confirm").value;
    if (pass !== confirm) {
        alert("Passphrases do not match!");
        return;
    }
    if (!pass) {
        alert("Passphrase cannot be empty!");
        return;
    }

    const btn = document.getElementById("btn-submit-create-wallet");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Generating & Encrypting...";
    }

    try {
        logActivity("Generating Post-Quantum SPHINCS+ Keypair & bcrypt KDF...", "info");
        const resp = await fetch("/api/wallet/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ passphrase: pass })
        });
        const data = await resp.json();
        if (data.status === "ok") {
            newlyCreatedWalletData = data;
            document.getElementById("created-native-address").innerText = data.address;
            document.getElementById("created-evm-address").innerText = data.evm_address;
            document.getElementById("created-wallet-result").style.display = "block";
            logActivity(`✅ Created SPHINCS+ Wallet: ${data.address.slice(0, 18)}...`, "success");
            document.getElementById("form-create-wallet").reset();
        } else {
            alert("Wallet creation failed: " + data.error);
        }
    } catch (err) {
        logActivity("Wallet creation error: " + err, "danger");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "Generate & Encrypt Wallet";
        }
    }
}

function downloadCreatedWallet() {
    if (!newlyCreatedWalletData || !newlyCreatedWalletData.wallet_json) return;
    const blob = new Blob([JSON.stringify(newlyCreatedWalletData.wallet_json, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "wallet.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    logActivity("Downloaded wallet.json to local machine (compatible with cli.py run --wallet wallet.json).", "success");
}

function useCreatedWalletAsActive() {
    if (!newlyCreatedWalletData) return;
    state.wallet.nativeAddress = newlyCreatedWalletData.address;
    state.wallet.evmAddress = newlyCreatedWalletData.evm_address;
    state.wallet.publicKeyHex = newlyCreatedWalletData.public_key;
    state.wallet.spendableBalance = 0;
    state.wallet.stakedBalance = 0;
    state.wallet.immatureBalance = 0;
    state.wallet.nonce = 0;
    updateWalletUI();
    syncWalletFromNode();
    logActivity(`Activated newly created wallet ${state.wallet.nativeAddress.slice(0, 18)}...`, "success");
}

function handleWalletFileSelect(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(evt) {
        try {
            const parsed = JSON.parse(evt.target.result);
            loadedWalletFileJson = parsed;
            document.getElementById("import-wallet-json-text").value = JSON.stringify(parsed, null, 2);
            logActivity(`Loaded wallet file: ${file.name}`, "info");
        } catch (err) {
            alert("Invalid JSON in wallet file: " + err);
        }
    };
    reader.readAsText(file);
}

async function handleUnlockWallet(e) {
    e.preventDefault();
    const pass = document.getElementById("import-wallet-passphrase").value;
    let wjson = loadedWalletFileJson;
    if (!wjson) {
        const text = document.getElementById("import-wallet-json-text").value.trim();
        if (text) {
            try {
                wjson = JSON.parse(text);
            } catch (err) {
                alert("Invalid wallet JSON text: " + err);
                return;
            }
        }
    }
    if (!wjson) {
        alert("Please select a wallet.json file or paste wallet JSON!");
        return;
    }

    const btn = document.getElementById("btn-submit-unlock-wallet");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Decrypting...";
    }

    try {
        logActivity("Decrypting wallet with bcrypt KDF...", "info");
        const resp = await fetch("/api/wallet/unlock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ passphrase: pass, wallet_json: wjson })
        });
        const data = await resp.json();
        if (data.status === "ok") {
            state.wallet.nativeAddress = data.address;
            state.wallet.evmAddress = data.evm_address;
            state.wallet.publicKeyHex = data.public_key;
            updateWalletUI();
            syncWalletFromNode();
            logActivity(`🔓 Wallet unlocked and set as active: ${data.address.slice(0, 18)}...`, "success");
            document.getElementById("form-unlock-wallet").reset();
        } else {
            alert("Failed to unlock wallet: " + data.error);
        }
    } catch (err) {
        logActivity("Wallet unlock error: " + err, "danger");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "Decrypt & Load Active Wallet";
        }
    }
}

async function syncWalletFromNode() {
    const res = await callNodeRPC("get_balance", { address: state.wallet.nativeAddress });
    if (res && res.data && !res.error) {
        const d = res.data;
        state.wallet.spendableBalance = d.spendable;
        state.wallet.stakedBalance = d.staked;
        state.wallet.immatureBalance = d.immature;
        state.wallet.nonce = d.next_nonce;
        updateWalletUI();
        logActivity(`Synced balance from node for ${state.wallet.nativeAddress.slice(0, 16)}...`, "info");
    }
}

function claimTestFaucet(amountPVO) {
    const addUnits = amountPVO * 100_000_000;
    state.wallet.spendableBalance += addUnits;
    updateWalletUI();
    logActivity(`Devnet Faucet credited +${amountPVO}.00000000 PVO to active wallet`, "success");
}

function handleSendTransaction(e) {
    e.preventDefault();
    const recipient = document.getElementById("tx-recipient").value.trim();
    const amountPVO = parseFloat(document.getElementById("tx-amount").value);
    const feePVO = parseFloat(document.getElementById("tx-fee").value);

    const totalNeeded = (amountPVO + feePVO) * 100_000_000;
    if (state.wallet.spendableBalance < totalNeeded) {
        logActivity(`Send failed: Insufficient spendable balance for amount + fee.`, "danger");
        alert("Insufficient balance!");
        return;
    }

    state.wallet.spendableBalance -= totalNeeded;
    state.wallet.nonce += 1;

    const txid = "0x" + Array.from(window.crypto.getRandomValues(new Uint8Array(32))).map(b => b.toString(16).padStart(2, "0")).join("");
    state.wallet.transactions.unshift({
        txid: txid,
        type: "PVO Transfer",
        recipient: recipient,
        amount: `${amountPVO.toFixed(8)} PVO`,
        fee: `${feePVO.toFixed(4)} PVO`,
        status: "CONFIRMED"
    });

    updateWalletUI();
    renderTxTable();
    logActivity(`Broadcasted Transaction ${txid.slice(0, 16)}... to ${recipient.slice(0, 14)}...`, "success");
    document.getElementById("form-send-tx").reset();
    document.getElementById("tx-fee").value = "0.00010000";
}

function renderTxTable() {
    const tbody = document.getElementById("tbody-tx-history");
    if (!tbody) return;
    tbody.innerHTML = state.wallet.transactions.map(tx => `
        <tr>
            <td><code class="mono">${tx.txid.slice(0, 16)}...</code></td>
            <td>${tx.type}</td>
            <td><code class="mono">${tx.recipient.slice(0, 14)}...</code></td>
            <td>${tx.amount}</td>
            <td>${tx.fee}</td>
            <td><span class="badge-mono chip-success">${tx.status}</span></td>
        </tr>
    `).join("");
}

// --- Native Node Miner & Consensus Telemetry Engine ---

let minerAutoPollTimer = null;

function startMinerAutoPoll() {
    if (minerAutoPollTimer) clearInterval(minerAutoPollTimer);
    minerAutoPollTimer = setInterval(() => {
        const chk = document.getElementById("miner-auto-poll-chk");
        if (chk && chk.checked) {
            fetchNodeMinerStatus();
        }
    }, 3000);
}

function toggleMinerAutoPoll(e) {
    if (e.target.checked) {
        startMinerAutoPoll();
        logActivity("Node auto-sync enabled (every 3s).", "info");
    } else {
        if (minerAutoPollTimer) clearInterval(minerAutoPollTimer);
        logActivity("Node auto-sync paused.", "info");
    }
}

async function fetchNodeMinerStatus() {
    const res = await callNodeRPC("pacvo_getStatus", {});
    if (res && res.data && !res.error) {
        const d = res.data;
        state.nodeMiner = d;
        state.blockchain.tipHeight = d.height;
        state.blockchain.prevHash = d.tip_hash;
        state.blockchain.activeTargetHex = d.target_hex;

        // Render live node info
        const elHeight = document.getElementById("miner-node-height");
        if (elHeight) elHeight.innerText = `#${d.height}`;
        const elDashHeight = document.getElementById("dash-block-height");
        if (elDashHeight) elDashHeight.innerText = `#${d.height}`;
        const elTipHash = document.getElementById("miner-tip-hash-preview");
        if (elTipHash) elTipHash.innerText = d.tip_hash ? d.tip_hash.slice(0, 16) + "..." : "Genesis";
        const elTarget = document.getElementById("miner-node-target");
        if (elTarget) elTarget.innerText = d.target_hex ? d.target_hex.slice(0, 16) + "..." : "...";
        const elWork = document.getElementById("miner-node-work");
        if (elWork) elWork.innerText = d.cumulative_work;
        const elMempool = document.getElementById("miner-node-mempool");
        if (elMempool) elMempool.innerText = `${d.mempool_size} Txs`;
        const elPeers = document.getElementById("miner-node-peers");
        if (elPeers) elPeers.innerText = d.peer_count;
        const elAddress = document.getElementById("miner-node-address");
        if (elAddress) elAddress.innerText = d.miner_address;

        const spendPvo = d.miner_spendable / 100_000_000;
        const stakePvo = d.miner_staked / 100_000_000;
        const totalPvo = spendPvo + stakePvo;
        const elRewards = document.getElementById("miner-node-rewards");
        if (elRewards) elRewards.innerText = `${totalPvo.toFixed(2)} PVO`;
        const elSpend = document.getElementById("miner-node-spendable");
        if (elSpend) elSpend.innerText = spendPvo.toFixed(2);
        const elStake = document.getElementById("miner-node-staked");
        if (elStake) elStake.innerText = stakePvo.toFixed(2);

        const badge = document.getElementById("miner-node-status-badge");
        if (badge) {
            badge.innerText = "● NODE ONLINE";
            badge.className = "badge-mono chip-success";
        }

        const engineBadge = document.getElementById("miner-engine-badge");
        if (engineBadge) {
            engineBadge.innerText = d.is_mining ? "MINING ACTIVE" : "NODE STANDBY";
            engineBadge.className = d.is_mining ? "badge-mono chip-warning" : "badge-mono chip-success";
        }

        // Render 128-block lockup from node state
        renderNodeLockupTable(d.miner_locked_entries || [], d.height);

        // Render recent blocks
        renderNodeRecentBlocksTable(d.recent_blocks || []);

        const syncTime = document.getElementById("miner-last-sync-time");
        if (syncTime) syncTime.innerText = `Last synced: ${new Date().toLocaleTimeString()}`;
    } else {
        const badge = document.getElementById("miner-node-status-badge");
        if (badge) {
            badge.innerText = "○ NODE OFFLINE";
            badge.className = "badge-mono chip-danger";
        }
    }
}

async function mineBlockOnNode() {
    logActivity("Dispatching mine block request to Pacvo Node...", "info");
    const btn = document.getElementById("btn-mine-node-block");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⛏ Mining block...";
    }
    try {
        const res = await callNodeRPC("pacvo_mineBlock", {});
        if (res && res.data && res.data.status === "MINED") {
            const b = res.data;
            logActivity(`✅ Mined Block #${b.height}! Hash: ${b.block_hash.slice(0, 16)}... Nonce: ${b.nonce}`, "success");
            logActivity(`   Coinbase credited: +1.50 PVO spendable, +1.50 PVO locked 128 blocks.`, "success");
            await fetchNodeMinerStatus();
            await syncWalletFromNode();
        } else {
            const err = res.error || (res.data && res.data.error) || "Mining rejected";
            logActivity(`Mining error: ${err}`, "danger");
        }
    } catch (err) {
        logActivity(`Mining error: ${err}`, "danger");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "⚡ Mine 1 Block on Node";
        }
    }
}

function renderNodeLockupTable(entries, currentHeight) {
    const tbody = document.getElementById("tbody-locked-rewards");
    if (!tbody) return;
    if (!entries || entries.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:1.5rem">No locked coinbase entries on node — mine blocks to generate rewards.</td></tr>`;
        const elPending = document.getElementById("miner-pending-locked");
        if (elPending) elPending.innerText = "0.00000000 PVO";
        const elUnlocked = document.getElementById("miner-unlocked-total");
        if (elUnlocked && state.nodeMiner) {
            elUnlocked.innerText = `${((state.nodeMiner.miner_spendable || 0) / 100_000_000).toFixed(8)} PVO`;
        }
        return;
    }

    let totalLocked = 0;
    tbody.innerHTML = entries.map(entry => {
        const amt = entry.amount / 100_000_000;
        totalLocked += entry.amount;
        const unlocksAt = entry.unlocks_at;
        const remaining = Math.max(0, unlocksAt - currentHeight);
        const pct = Math.min(100, Math.round(((128 - remaining) / 128) * 100));
        const statusHtml = remaining === 0
            ? `<span class="badge-mono chip-success">MATURED</span>`
            : `<span class="badge-mono chip-warning">LOCKED</span>`;
        return `<tr>
            <td><strong>${amt.toFixed(8)} PVO</strong></td>
            <td>#${unlocksAt}</td>
            <td>${remaining} blks</td>
            <td>
                <div style="background:var(--bg-surface-raised);border-radius:4px;height:6px;width:100px;overflow:hidden;display:inline-block;vertical-align:middle;margin-right:6px">
                    <div style="background:var(--status-warning);height:100%;width:${pct}%"></div>
                </div>${pct}%
            </td>
            <td>${statusHtml}</td>
        </tr>`;
    }).join("");

    const elPending = document.getElementById("miner-pending-locked");
    if (elPending) elPending.innerText = `${(totalLocked / 100_000_000).toFixed(8)} PVO`;
    const elUnlocked = document.getElementById("miner-unlocked-total");
    if (elUnlocked && state.nodeMiner) {
        elUnlocked.innerText = `${((state.nodeMiner.miner_spendable || 0) / 100_000_000).toFixed(8)} PVO`;
    }
}

function renderNodeRecentBlocksTable(blocks) {
    const tbody = document.getElementById("tbody-node-blocks");
    if (!tbody) return;
    if (!blocks || blocks.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1.5rem">No recent blocks on node.</td></tr>`;
        return;
    }
    tbody.innerHTML = blocks.map(b => `
        <tr>
            <td><strong>#${b.height}</strong></td>
            <td><code class="mono">${b.block_hash ? b.block_hash.slice(0, 20) : "-"}...</code></td>
            <td><code class="mono">${b.nonce}</code></td>
            <td>${b.tx_count} tx(s)</td>
            <td>${new Date(b.timestamp * 1000).toLocaleTimeString()}</td>
            <td><span class="badge-mono chip-success">CONFIRMED</span></td>
        </tr>
    `).join("");
}

// --- Layer 2 Studio (Tokens & NFTs) ---

function handleDeployToken(e) {
    e.preventDefault();
    const name = document.getElementById("token-name").value.trim();
    const symbol = document.getElementById("token-symbol").value.trim().toUpperCase();
    const type = document.getElementById("token-type").value === "fixed" ? "Fixed Supply" : "Controlled Mint";
    const supply = document.getElementById("token-supply").value;
    const saltInput = document.getElementById("token-salt").value.trim();

    const salt = saltInput || ("0x" + Array.from(window.crypto.getRandomValues(new Uint8Array(32))).map(b => b.toString(16).padStart(2, "0")).join(""));
    const contractAddr = PacvoCrypto.computeCreate2Address(state.wallet.evmAddress, salt, "608060405234801561001057600080fd5b50");

    state.l2.tokens.push({
        symbol: symbol,
        name: name,
        address: contractAddr,
        totalSupply: Number(supply).toLocaleString(),
        myBalance: Number(supply).toLocaleString(),
        type: type,
        decimals: 18
    });

    renderL2Tokens();
    logActivity(`Deployed ERC-20 Token [${symbol}] ${name} at ${contractAddr} via CREATE2.`, "success");
    document.getElementById("form-deploy-token").reset();
}

function handleTokenOperation(e) {
    e.preventDefault();
    const tokenSymbol = document.getElementById("token-select-op").value;
    const opType = document.getElementById("token-op-type").value;
    const amount = parseFloat(document.getElementById("token-op-amount").value);
    const recipient = document.getElementById("token-op-recipient").value.trim() || state.wallet.evmAddress;

    const token = state.l2.tokens.find(t => t.symbol === tokenSymbol);
    if (!token) return;

    let curBal = parseFloat(token.myBalance.replace(/,/g, ""));
    if (opType === "transfer" || opType === "burn") {
        if (curBal < amount) {
            alert("Insufficient token balance!");
            return;
        }
        token.myBalance = (curBal - amount).toLocaleString();
    } else if (opType === "mint") {
        token.myBalance = (curBal + amount).toLocaleString();
    }

    renderL2Tokens();
    logActivity(`ERC-20 Operation: ${opType.toUpperCase()} ${amount} ${tokenSymbol} (${recipient.slice(0, 10)}...)`, "success");
    document.getElementById("form-token-ops").reset();
}

function renderL2Tokens() {
    const tbody = document.getElementById("tbody-tokens-list");
    const select = document.getElementById("token-select-op");
    if (!tbody || !select) return;

    tbody.innerHTML = state.l2.tokens.map(t => `
        <tr>
            <td><strong>${t.symbol}</strong></td>
            <td>${t.name}</td>
            <td><code class="mono">${t.address.slice(0, 16)}...</code></td>
            <td>${t.totalSupply}</td>
            <td><strong style="color: var(--status-success);">${t.myBalance}</strong></td>
            <td><span class="badge-mono">${t.type}</span></td>
        </tr>
    `).join("");

    select.innerHTML = state.l2.tokens.map(t => `<option value="${t.symbol}">${t.symbol} — ${t.name}</option>`).join("");
    document.getElementById("dash-l2-token-count").innerText = state.l2.tokens.length;
}

function handleDeployNFTCollection(e) {
    e.preventDefault();
    const name = document.getElementById("nft-col-name").value.trim();
    const symbol = document.getElementById("nft-col-symbol").value.trim().toUpperCase();
    const colAddr = "0x" + Array.from(window.crypto.getRandomValues(new Uint8Array(20))).map(b => b.toString(16).padStart(2, "0")).join("");

    state.l2.nftCollections.push({
        name: name,
        symbol: symbol,
        address: colAddr,
        owner: state.wallet.evmAddress,
        totalMinted: 0
    });

    renderNFTCollections();
    logActivity(`Deployed ERC-721 Collection [${symbol}] at ${colAddr}.`, "success");
    document.getElementById("form-deploy-nft-col").reset();
}

function handleMintNFT(e) {
    e.preventDefault();
    const colSymbol = document.getElementById("nft-mint-col-select").value;
    const tokenId = parseInt(document.getElementById("nft-mint-id").value);
    const recipient = document.getElementById("nft-mint-recipient").value.trim();
    const uri = document.getElementById("nft-mint-uri").value.trim() || `Artifact #${tokenId}`;

    const icons = ["💎", "🛡️", "⚡", "💠", "🔮", "🧬", "🌟", "📜"];
    const randIcon = icons[tokenId % icons.length];

    state.l2.nfts.push({
        collection: colSymbol,
        tokenId: tokenId,
        name: uri,
        owner: recipient,
        icon: randIcon,
        uri: `ipfs://pacvo/${tokenId}`
    });

    renderNFTGallery();
    logActivity(`Minted NFT #${tokenId} [${colSymbol}] to ${recipient.slice(0, 12)}...`, "success");
    document.getElementById("form-mint-nft").reset();
}

function renderNFTCollections() {
    const select = document.getElementById("nft-mint-col-select");
    if (!select) return;
    select.innerHTML = state.l2.nftCollections.map(c => `<option value="${c.symbol}">${c.name} (${c.symbol})</option>`).join("");
}

function renderNFTGallery() {
    const grid = document.getElementById("nft-gallery-grid");
    if (!grid) return;

    grid.innerHTML = state.l2.nfts.map(nft => `
        <div class="nft-card">
            <div class="nft-visual">${nft.icon}</div>
            <div class="nft-details">
                <div class="nft-name">${escapeHtml(nft.name)}</div>
                <div class="nft-meta">Token ID: <strong>#${nft.tokenId}</strong> (${nft.collection})</div>
                <div class="nft-meta">Owner: <code class="mono">${nft.owner.slice(0, 10)}...</code></div>
            </div>
        </div>
    `).join("");

    document.getElementById("nft-total-badge").innerText = `Total: ${state.l2.nfts.length}`;
    document.getElementById("dash-l2-nft-count").innerText = state.l2.nfts.length;
}

// --- Layer 3 PVO-Fi Hub (Reserve, AMM, Lending, Bridges) ---

function simulateAttestation(verifiedPOL) {
    state.l3.reserve.verifiedPOL = verifiedPOL;
    state.l3.reserve.isSolvent = verifiedPOL >= state.l3.reserve.floorPOL && verifiedPOL >= state.l3.reserve.encumberedPOL;

    const badge = document.getElementById("reserve-solvency-badge");
    const dashStatus = document.getElementById("dash-solvency-status");
    if (state.l3.reserve.isSolvent) {
        badge.innerText = "100% SOLVENT";
        badge.className = "badge-mono chip-success";
        dashStatus.innerText = "SOLVENT";
        dashStatus.style.color = "var(--status-success)";
        logActivity(`Proof-of-Reserve Attestation: ${verifiedPOL.toFixed(4)} POL verified. Solvency Invariant satisfied.`, "success");
    } else {
        badge.innerText = "SOLVENCY WARNING";
        badge.className = "badge-mono chip-danger";
        dashStatus.innerText = "BREACH";
        dashStatus.style.color = "var(--status-danger)";
        logActivity(`SOLVENCY BREACH! Verified balance ${verifiedPOL.toFixed(4)} POL is below 4.0 POL floor or active encumbrance.`, "danger");
    }
}

function updateSwapQuote() {
    const tokenIn = document.getElementById("swap-token-in").value;
    const amtIn = parseFloat(document.getElementById("swap-amount-in").value) || 0;
    const quoteEl = document.getElementById("swap-quote-output");

    if (amtIn <= 0) {
        quoteEl.innerText = "0.0000";
        return;
    }

    const { reservePOL, reservePVOA } = state.l3.amm;
    const rIn = (tokenIn === "POL") ? reservePOL : reservePVOA;
    const rOut = (tokenIn === "POL") ? reservePVOA : reservePOL;

    // AMM with 30 bps fee: dy = (rOut * dx * 9970) / (rIn * 10000 + dx * 9970)
    const netDx = amtIn * 0.9970;
    const dy = (rOut * netDx) / (rIn + netDx);
    quoteEl.innerText = dy.toFixed(4);
}

function handleAmmSwap(e) {
    e.preventDefault();
    const tokenIn = document.getElementById("swap-token-in").value;
    const amtIn = parseFloat(document.getElementById("swap-amount-in").value);
    const quoteAmt = parseFloat(document.getElementById("swap-quote-output").innerText);

    if (tokenIn === "POL") {
        state.l3.amm.reservePOL += amtIn;
        state.l3.amm.reservePVOA -= quoteAmt;
    } else {
        state.l3.amm.reservePVOA += amtIn;
        state.l3.amm.reservePOL -= quoteAmt;
    }

    // Monotonically increasing invariant k
    state.l3.amm.invariantK = state.l3.amm.reservePOL * state.l3.amm.reservePVOA;

    renderAmmPoolUI();
    logActivity(`AMM Swap Executed: Sold ${amtIn} ${tokenIn} -> Received ${quoteAmt.toFixed(4)} (30 bps LP fee retained in pool).`, "success");
    document.getElementById("form-amm-swap").reset();
    updateSwapQuote();
}

function renderAmmPoolUI() {
    document.getElementById("pool-reserve-pol").innerText = state.l3.amm.reservePOL.toLocaleString(undefined, { minimumFractionDigits: 4 });
    document.getElementById("pool-reserve-pvoa").innerText = state.l3.amm.reservePVOA.toLocaleString(undefined, { minimumFractionDigits: 4 });
    document.getElementById("pool-invariant-k").innerText = state.l3.amm.invariantK.toLocaleString(undefined, { minimumFractionDigits: 2 });
    const spot = state.l3.amm.reservePVOA / state.l3.amm.reservePOL;
    document.getElementById("pool-spot-price").innerText = `${spot.toFixed(4)} PVOA / POL`;
}

function handleCreatePosition(e) {
    e.preventDefault();
    const collat = parseFloat(document.getElementById("lend-collat-amount").value);
    const debt = parseFloat(document.getElementById("lend-debt-amount").value);

    state.l3.lending.collatAmount = collat;
    state.l3.lending.debtAmount = debt;
    state.l3.lending.collatPrice = 1.0;

    updateLendingHealthUI();
    logActivity(`Opened Collateral Position: ${collat} COLLAT deposited, ${debt} POL borrowed.`, "info");
}

function simulatePriceCrash() {
    state.l3.lending.collatPrice = 0.40; // 60% drop to $0.40
    updateLendingHealthUI();
    logActivity("Simulated 60% collateral price crash ($1.00 -> $0.40). Health factor dropped below 1.0 WAD.", "warning");
}

function updateLendingHealthUI() {
    const { collatAmount, debtAmount, collatPrice, debtPrice, liqThresholdBps } = state.l3.lending;
    const collatVal = collatAmount * collatPrice;
    const debtVal = debtAmount * debtPrice;
    const adjCollat = (collatVal * liqThresholdBps) / 10000;
    const hf = debtVal > 0 ? adjCollat / debtVal : 100.0;

    const hfEl = document.getElementById("pos-health-val");
    const badge = document.getElementById("pos-health-badge");
    const liqBtn = document.getElementById("btn-liquidate-pos");

    if (hfEl) {
        hfEl.innerText = hf.toFixed(4);
        if (hf < 1.0) {
            hfEl.style.color = "var(--status-danger)";
        } else {
            hfEl.style.color = "var(--status-success)";
        }
    }
    if (badge) {
        if (hf < 1.0) {
            badge.innerText = "LIQUIDATABLE";
            badge.className = "badge-mono chip-danger";
        } else {
            badge.innerText = "HEALTHY";
            badge.className = "badge-mono chip-success";
        }
    }
    if (liqBtn) {
        liqBtn.disabled = (hf >= 1.0);
    }
}

function liquidatePosition() {
    const repaidDebt = state.l3.lending.debtAmount * 0.50; // Cover 50%
    state.l3.lending.debtAmount -= repaidDebt;
    state.l3.lending.collatAmount -= (repaidDebt * 1.10) / state.l3.lending.collatPrice; // Seize with 10% bonus
    state.l3.lending.collatPrice = 1.0; // Restore baseline

    updateLendingHealthUI();
    logActivity(`Liquidated 50% undercollateralized debt position. Liquidator claimed 10% liquidation bonus.`, "success");
}

function declareEquityDividend(payout) {
    const deltaIndex = payout / state.l3.equity.totalSupply;
    state.l3.equity.cumulativePerShare += deltaIndex;
    const delta = state.l3.equity.cumulativePerShare - state.l3.equity.userLastIndex;
    state.l3.equity.claimableDividend = state.l3.equity.userBalance * delta;

    document.getElementById("equity-claimable-val").innerText = `${state.l3.equity.claimableDividend.toFixed(4)} POL`;
    logActivity(`Declared $${payout} Dividend for EQPOCH. Global index updated in O(1) time.`, "success");
}

function claimEquityDividend() {
    const amt = state.l3.equity.claimableDividend;
    if (amt <= 0) {
        alert("No claimable dividends.");
        return;
    }
    state.l3.equity.userLastIndex = state.l3.equity.cumulativePerShare;
    state.l3.equity.claimableDividend = 0;
    document.getElementById("equity-claimable-val").innerText = "0.0000 POL";
    logActivity(`Claimed ${amt.toFixed(4)} POL dividend payout.`, "success");
}

function simulateBridgeDeposit(symbol, amount) {
    if (symbol === "BTC") {
        state.l3.bridges.btcLockedSat += amount;
        logActivity(`Bitcoin Bridge: Attested +${(amount / 100_000_000).toFixed(8)} BTC locked in vault. Minted wPVO-BTC.`, "success");
    } else if (symbol === "XNO") {
        state.l3.bridges.xnoLockedRaw += BigInt(amount) * 10n ** 30n;
        logActivity(`Nano Bridge: Attested +${amount} XNO locked in vault. Minted wPVO-XNO.`, "success");
    } else if (symbol === "CC") {
        state.l3.bridges.ccLockedRaw += amount;
        logActivity(`Chocohub Bridge: Attested +${(amount / 100_000_000).toFixed(4)} CC locked in vault. Minted wCCPVO.`, "success");
    }
}

function simulateBridgeBurn(symbol, amount) {
    logActivity(`${symbol} Bridge: Burned ${amount} wrapped asset. Vault unlock initiated.`, "info");
}

// --- JSON-RPC Gateway Query ---

function handleRpcMethodTemplateChange(e) {
    const m = e.target.value;
    const input = document.getElementById("rpc-params-input");
    if (m === "get_balance") input.value = JSON.stringify({ address: state.wallet.nativeAddress });
    else if (m === "get_block") input.value = JSON.stringify({ height: state.blockchain.tipHeight });
    else if (m === "get_tip") input.value = "{}";
    else if (m === "pacvo_l2_getToken") input.value = JSON.stringify({ symbol: "PUSD" });
    else if (m === "pacvo_l2_getNFT") input.value = JSON.stringify({ collection: "PVONFT", token_id: 1 });
    else if (m === "pacvo_l2_getNFTCollection") input.value = JSON.stringify({ symbol: "PVONFT" });
    else if (m === "pacvo_l3_getReserve") input.value = "{}";
    else if (m === "pacvo_l3_getMarket") input.value = JSON.stringify({ symbol_a: "POL", symbol_b: "PVOA" });
    else if (m === "pacvo_l3_getBridge") input.value = "{}";
}

async function executeRpcQuery() {
    const method = document.getElementById("rpc-method-select").value;
    const paramsStr = document.getElementById("rpc-params-input").value;
    const outputEl = document.getElementById("rpc-response-output");

    let params = {};
    try {
        params = JSON.parse(paramsStr);
    } catch (err) {
        outputEl.value = `Error parsing parameters JSON: ${err.message}`;
        return;
    }

    outputEl.value = "Executing JSON-RPC query on node 127.0.0.1:9442...";
    const res = await callNodeRPC(method, params);
    outputEl.value = JSON.stringify(res, null, 2);
    logActivity(`JSON-RPC Query [${method}] executed against node.`, "info");
}

async function testRpcConnection() {
    const res = await callNodeRPC("pacvo_getStatus", {});
    if (res && res.data && !res.error) {
        logActivity(`RPC Gateway verified — Connected to Pacvo Node (Height #${res.data.height}).`, "success");
        alert(`RPC Connection Verified — Connected to Pacvo Node (Height #${res.data.height}, Work: ${res.data.cumulative_work})`);
    } else {
        logActivity(`RPC Gateway error: ${res.error || 'Node unreachable'}`, "danger");
        alert(`RPC Gateway error: ${res.error || 'Node unreachable'}`);
    }
}

// --- HTLC & Chocohub (CC) Atomic Swap Logic ---

const CHOCO_DEVICE_MULTIPLIERS_JS = {
    "cpu": 3.0,
    "embedded_avr": 3.5,
    "embedded_arm": 3.5,
    "embedded_esp": 2.5,
    "embedded_esp32": 2.0,
    "mobile": 3.6,
    "gpu": 2.0
};

async function generateNewHtlcSecret() {
    try {
        const { secretHex, hashlockHex } = await PacvoCrypto.generateHTLCSecret();
        state.htlc.currentSecret = secretHex;
        state.htlc.currentHashlock = hashlockHex;

        const secretInput = document.getElementById("htlc-secret-input");
        const hashlockInput = document.getElementById("htlc-hashlock-input");
        if (secretInput) secretInput.value = secretHex;
        if (hashlockInput) hashlockInput.value = hashlockHex;

        logActivity(`Generated new HTLC 32-byte secret & SHA-256 hashlock (${hashlockHex.slice(0, 16)}...).`, "info");
    } catch (e) {
        logActivity(`Secret generation failed: ${e.message}`, "error");
    }
}

function updateHtlcCcCalculation(e) {
    const pvo = parseFloat(document.getElementById("htlc-amount-pvo").value) || 0;
    const cc = (pvo * 10).toFixed(4);
    const preview = document.getElementById("htlc-amount-cc-preview");
    if (preview) preview.value = `${cc} CC`;
}

async function executeCreateHtlcOrder() {
    const initPacvo = state.wallet.evmAddress;
    const partChoco = document.getElementById("htlc-part-choco").value.trim() || "choco_trader";
    const amountPvo = parseFloat(document.getElementById("htlc-amount-pvo").value) || 0;
    const autoMine = document.getElementById("htlc-auto-mine-checkbox").checked;

    if (amountPvo <= 0) {
        alert("Enter a valid PVO amount");
        return;
    }
    const pvoUnits = amountPvo * 100_000_000;
    if (state.wallet.spendableBalance < pvoUnits) {
        alert(`Insufficient PVO balance. Have ${state.wallet.spendableBalance / 100_000_000} PVO, requires ${amountPvo} PVO`);
        return;
    }

    if (!state.htlc.currentSecret || !state.htlc.currentHashlock) {
        await generateNewHtlcSecret();
    }

    const amountCc = amountPvo * 10;
    const orderId = "0x" + Array.from(window.crypto.getRandomValues(new Uint8Array(8))).map(b => b.toString(16).padStart(2, "0")).join("");

    // Debit PVO into escrow
    state.wallet.spendableBalance -= pvoUnits;
    state.htlc.escrowPvo += amountPvo;
    state.htlc.escrowCc += amountCc;

    let minedCc = 0;
    let minedProofs = 0;
    if (autoMine) {
        // Mine 10 CC per 1 PVO immediately upon swap
        const proof = await PacvoCrypto.solveCCPoWProof(state.blockchain.prevHash, partChoco, 1.0, 500);
        minedCc = amountCc;
        minedProofs = 1;
        state.htlc.totalMinedCc += minedCc;
        state.htlc.solvedProofs += 1;
        state.htlc.lastProofHash = proof.hash;
        logActivity(`[CCpow] Mined ${amountCc.toFixed(2)} CC (10 CC/PVO) on swap ${orderId}. Hash: ${proof.hash.slice(0, 16)}...`, "success");
    }

    const newOrder = {
        orderId: orderId,
        initiatorPacvo: initPacvo,
        participantChoco: partChoco,
        amountPvo: amountPvo,
        amountCc: amountCc,
        hashlockHex: state.htlc.currentHashlock,
        secretHex: state.htlc.currentSecret,
        timelockPacvo: state.blockchain.tipHeight + 144,
        timelockChoco: state.blockchain.tipHeight + 72,
        minedProofs: minedProofs,
        status: "LOCKED",
        ccMinedWad: minedCc
    };

    state.htlc.orders.unshift(newOrder);
    logActivity(`HTLC Atomic Swap ${orderId} locked for ${amountPvo} PVO &rarr; ${amountCc} CC. Hashlock published.`, "success");

    updateWalletUI();
    renderHtlcUI();
    populateHtlcTargetOrders();
}

async function claimHtlcOrder(orderId) {
    const order = state.htlc.orders.find(o => o.orderId === orderId);
    if (!order || order.status !== "LOCKED") return;

    let secret = order.secretHex;
    if (!secret) {
        secret = prompt(`Enter 32-byte secret pre-image for HTLC order ${orderId}:`);
        if (!secret) return;
    }

    // Verify secret
    const secretBytes = new Uint8Array(secret.replace(/^0x/, "").match(/.{1,2}/g).map(byte => parseInt(byte, 16)));
    const checkHash = await PacvoCrypto.sha256Hex(secretBytes);
    if (checkHash !== order.hashlockHex) {
        alert(`Invalid pre-image secret! SHA256(${secret}) does not match hashlock.`);
        return;
    }

    // Settle atomic swap
    state.htlc.escrowPvo -= order.amountPvo;
    state.htlc.escrowCc -= order.amountCc;
    state.htlc.totalSwappedPvo += order.amountPvo;
    order.status = "CLAIMED";
    order.secretHex = secret;

    logActivity(`HTLC Order ${orderId} CLAIMED via pre-image revelation. Settled ${order.amountPvo} PVO to participant and ${order.amountCc} CC to initiator.`, "success");
    renderHtlcUI();
}

function refundHtlcOrder(orderId) {
    const order = state.htlc.orders.find(o => o.orderId === orderId);
    if (!order || order.status !== "LOCKED") return;

    state.htlc.escrowPvo -= order.amountPvo;
    state.htlc.escrowCc -= order.amountCc;
    state.wallet.spendableBalance += order.amountPvo * 100_000_000;
    order.status = "REFUNDED";

    logActivity(`HTLC Order ${orderId} REFUNDED after timelock expiry. Returned ${order.amountPvo} PVO to initiator.`, "info");
    updateWalletUI();
    renderHtlcUI();
}

function handleHtlcDeviceChange(e) {
    state.htlc.miningTier = e.target.value;
    const mult = CHOCO_DEVICE_MULTIPLIERS_JS[state.htlc.miningTier] || 1.0;
    logActivity(`CCpow hardware tier set to [${state.htlc.miningTier}] (${mult}x device multiplier).`, "info");
}

function populateHtlcTargetOrders() {
    const select = document.getElementById("htlc-target-order-select");
    if (!select) return;
    const active = state.htlc.orders.filter(o => o.status === "LOCKED");
    select.innerHTML = `<option value="auto">Auto (Mine on Next Active Swap)</option>` +
        active.map(o => `<option value="${o.orderId}">${o.orderId.slice(0, 10)}... (${o.amountPvo} PVO &rarr; ${o.amountCc} CC)</option>`).join("");
}

async function simulateSingleHtlcProof() {
    const mult = CHOCO_DEVICE_MULTIPLIERS_JS[state.htlc.miningTier] || 1.0;
    const worker = state.htlc.worker || "pacvo15_476_wccpvo";
    const proof = await PacvoCrypto.solveCCPoWProof(state.blockchain.prevHash, worker, 2.0, 2000);
    const rewardCc = 0.05 * mult;
    state.htlc.solvedProofs += 1;
    state.htlc.totalMinedCc += rewardCc;
    state.htlc.lastProofHash = proof.hash;

    const hashInput = document.getElementById("htlc-miner-last-hash");
    if (hashInput) hashInput.value = `${proof.hash.slice(0, 24)}... (Nonce: ${proof.nonce})`;

    logActivity(`[CCpow MPG_Miner] Solved block proof for ${worker}! Hash: ${proof.hash.slice(0, 16)}... Nonce: ${proof.nonce}. Earned +${rewardCc.toFixed(3)} CC (${mult}x multiplier).`, "success");
    renderHtlcUI();
}

function toggleHtlcMining() {
    const btn = document.getElementById("btn-start-htlc-miner");
    const badge = document.getElementById("htlc-miner-status-badge");

    if (state.htlc.isMining) {
        clearInterval(state.htlc.miningInterval);
        state.htlc.isMining = false;
        if (btn) btn.innerText = "Start CCpow Swap Miner";
        if (badge) {
            badge.innerText = "Idle";
            badge.className = "badge badge-primary";
        }
        document.getElementById("htlc-miner-hashrate").innerText = "0 H/s";
        logActivity("Stopped CCpow Swap Miner.", "info");
    } else {
        state.htlc.isMining = true;
        if (btn) btn.innerText = "Stop CCpow Swap Miner";
        if (badge) {
            badge.innerText = "Mining CCpow";
            badge.className = "badge badge-success";
        }

        const mult = CHOCO_DEVICE_MULTIPLIERS_JS[state.htlc.miningTier] || 1.0;
        const worker = state.htlc.worker || "pacvo15_476_wccpvo";
        let hashesInWindow = 0;

        state.htlc.miningInterval = setInterval(async () => {
            const batch = 120;
            hashesInWindow += batch;
            const rate = (hashesInWindow * (0.8 + Math.random() * 0.4)).toFixed(0);
            document.getElementById("htlc-miner-hashrate").innerText = `${rate} H/s`;

            if (Math.random() < 0.25) {
                const proof = await PacvoCrypto.solveCCPoWProof(state.blockchain.prevHash, worker, 1.5, 100);
                const rewardCc = 0.05 * mult;
                state.htlc.solvedProofs += 1;
                state.htlc.totalMinedCc += rewardCc;
                state.htlc.lastProofHash = proof.hash;

                const hashInput = document.getElementById("htlc-miner-last-hash");
                if (hashInput) hashInput.value = `${proof.hash.slice(0, 24)}... (Nonce: ${proof.nonce})`;

                renderHtlcUI();
            }
            hashesInWindow = 0;
        }, 1000);

        logActivity(`Started CCpow MPG_Miner background loop on active HTLC orders for ${worker} (${mult}x multiplier).`, "success");
    }
}

function renderHtlcOrdersTable() {
    const tbody = document.getElementById("htlc-orders-tbody");
    if (!tbody) return;

    if (state.htlc.orders.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">No HTLC swap orders yet. Use the form above to lock a swap (1 PVO = 10 CC).</td></tr>`;
        return;
    }

    tbody.innerHTML = state.htlc.orders.map(o => {
        let statusBadge = `<span class="badge badge-warning">LOCKED</span>`;
        if (o.status === "CLAIMED") statusBadge = `<span class="badge badge-success">CLAIMED</span>`;
        if (o.status === "REFUNDED") statusBadge = `<span class="badge badge-primary">REFUNDED</span>`;

        let actionBtns = ``;
        if (o.status === "LOCKED") {
            actionBtns = `
                <div style="display: flex; gap: 0.3rem;">
                    <button class="btn btn-primary" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;" onclick="claimHtlcOrder('${o.orderId}')">Claim</button>
                    <button class="btn btn-secondary" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;" onclick="refundHtlcOrder('${o.orderId}')">Refund</button>
                </div>
            `;
        } else {
            actionBtns = `<span style="font-size: 0.75rem; color: var(--text-muted);">&mdash;</span>`;
        }

        return `
            <tr>
                <td class="mono" style="font-size: 0.75rem;">${o.orderId.slice(0, 10)}...</td>
                <td class="mono font-semibold">${o.amountPvo.toFixed(2)} PVO</td>
                <td class="mono font-semibold" style="color: var(--status-success);">${o.amountCc.toFixed(2)} CC</td>
                <td style="font-size: 0.75rem;">1 PVO = 10 CC</td>
                <td class="mono" style="font-size: 0.7rem;">${o.hashlockHex.slice(0, 12)}...</td>
                <td style="font-size: 0.75rem;">Block #${o.timelockPacvo}</td>
                <td style="font-size: 0.75rem;">${o.minedProofs} proofs</td>
                <td>${statusBadge}</td>
                <td>${actionBtns}</td>
            </tr>
        `;
    }).join("");
}

function renderHtlcUI() {
    const escrowPvoEl = document.getElementById("htlc-escrow-pvo");
    const escrowCcEl = document.getElementById("htlc-escrow-cc");
    const totalSwappedEl = document.getElementById("htlc-total-swapped");
    const totalMinedEl = document.getElementById("htlc-total-mined-cc");
    const proofsEl = document.getElementById("htlc-miner-proofs");
    const initPacvoEl = document.getElementById("htlc-init-pacvo");

    if (escrowPvoEl) escrowPvoEl.innerText = `${state.htlc.escrowPvo.toFixed(4)} PVO`;
    if (escrowCcEl) escrowCcEl.innerText = `${state.htlc.escrowCc.toFixed(4)} CC`;
    if (totalSwappedEl) totalSwappedEl.innerText = `${state.htlc.totalSwappedPvo.toFixed(2)} PVO`;
    if (totalMinedEl) totalMinedEl.innerText = `${state.htlc.totalMinedCc.toFixed(4)} CC`;
    if (proofsEl) proofsEl.innerText = state.htlc.solvedProofs;
    if (initPacvoEl) initPacvoEl.value = state.wallet.evmAddress;

    renderHtlcOrdersTable();
}

// --- Render All ---

function renderAll() {
    updateWalletUI();
    renderL2Tokens();
    renderNFTCollections();
    renderNFTGallery();
    renderAmmPoolUI();
    updateLendingHealthUI();
    renderHtlcUI();
    populateHtlcTargetOrders();
}
