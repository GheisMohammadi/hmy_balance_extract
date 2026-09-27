package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"sort"
	"strings"
	"syscall"
	"time"
)

type cutoff struct {
	Block uint64 `json:"block"`
	Hash  string `json:"hash"`
	Root  string `json:"state_root"`
}
type manifest struct {
	Cutoff struct {
		Shard0 cutoff `json:"shard0"`
		Shard1 cutoff `json:"shard1"`
	} `json:"cutoff"`
}
type report struct {
	AllAccounts  bool                         `json:"all_accounts,omitempty"`
	Schema       string                       `json:"schema"`
	Mode         string                       `json:"mode"`
	Complete     bool                         `json:"complete"`
	Shard        int                          `json:"shard"`
	Cutoff       cutoff                       `json:"cutoff"`
	ManifestSHA  string                       `json:"manifest_sha256"`
	ExtractorSHA string                       `json:"extractor_sha256"`
	SelectionSHA string                       `json:"selection_sha256"`
	DB           string                       `json:"database"`
	Accounts     map[string]map[string]string `json:"accounts"`
	Outgoing     []receipt                    `json:"outgoing,omitempty"`
	Incoming     []receipt                    `json:"incoming,omitempty"`
	Counts       map[string]uint64            `json:"counts"`
	Elapsed      float64                      `json:"elapsed_seconds"`
	Limitations  []string                     `json:"limitations,omitempty"`
}

func numbered(prefix byte, n uint64, suffix []byte) []byte {
	b := make([]byte, 9)
	b[0] = prefix
	binary.BigEndian.PutUint64(b[1:], n)
	return append(b, suffix...)
}
func tagged(x item) (string, []item) {
	require(x.list, "expected tagged list")
	if len(x.children) == 3 && !x.children[0].list && string(x.children[0].data) == "HmnyTgd" {
		require(x.children[2].list, "tag payload must be list")
		return string(scalar(x.children[1])), x.children[2].children
	}
	return "v0", x.children
}

type header struct {
	root, parent, out, in []byte
	number, shard         uint64
}

func parseHeader(raw []byte) header {
	tag, f := tagged(decode(raw))
	require(tag == "v0" || tag == "v1" || tag == "v2" || tag == "v3", "unsupported header tag")
	ni, si := 8, 16
	if tag == "v0" {
		ni, si = 6, 14
	}
	require(len(f) > si, "short header")
	h := header{root: scalar(f[2]), parent: scalar(f[0]), number: number(f[ni]), shard: number(f[si]), out: emptyTrie, in: emptyTrie}
	require(len(h.root) == 32 && len(h.parent) == 32, "invalid header hash length")
	if tag != "v0" {
		h.out = scalar(f[5])
		h.in = scalar(f[6])
		require(len(h.out) == 32 && len(h.in) == 32, "invalid receipt root")
	}
	return h
}
func readHeader(db kvReader, n uint64, hash []byte) header {
	raw, ok := db.get(numbered('h', n, hash))
	require(ok, "missing canonical header")
	require(bytes.Equal(keccak(raw), hash), "header hash mismatch")
	h := parseHeader(raw)
	require(h.number == n, "header height mismatch")
	return h
}
func verifyCutoff(db kvReader, c cutoff, shard int) header {
	hash := unhex(c.Hash, 32)
	root := unhex(c.Root, 32)
	got, ok := db.get(numbered('h', c.Block, []byte{'n'}))
	require(ok && bytes.Equal(got, hash), "cutoff canonical hash mismatch")
	h := readHeader(db, c.Block, hash)
	require(h.shard == uint64(shard) && bytes.Equal(h.root, root), "cutoff shard/state root mismatch")
	return h
}
func saveReport(path string, r report) {
	b, e := json.MarshalIndent(r, "", "  ")
	check(e)
	partial := path + ".partial"
	f, e := os.OpenFile(partial, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	check(e)
	defer func() { f.Close(); os.Remove(partial) }()
	_, e = f.Write(append(b, '\n'))
	check(e)
	check(f.Sync())
	check(f.Close())
	// Linking publishes completed evidence without replacing an existing path.
	check(os.Link(partial, path))
}

func run() {
	dbPath := flag.String("db", "", "consistent, offline LevelDB/checkpoint (opened read-only)")
	manifestPath := flag.String("manifest", "", "cutoff manifest JSON")
	accountsPath := flag.String("accounts", "", "text file, one 0x address per line; required")
	output := flag.String("output", "", "new evidence JSON path")
	shard := flag.Int("shard", -1, "0 or 1")
	mode := flag.String("mode", "probe", "probe (liquid/WONE only), state (also staking), or receipts")
	probeValidators := flag.String("probe-validators", "", "probe only: optional text file of validator addresses; produces PARTIAL staking sums")
	allowScan := flag.Bool("allow-global-scan", false, "explicitly permit whole state/history scans for selected accounts")
	maxAccounts := flag.Int("max-accounts", 20, "hard cap on selected account count")
	timeout := flag.Duration("timeout", 2*time.Minute, "maximum run duration; increase explicitly for global scans")
	cache := flag.Int("cache-mb", 128, "LevelDB cache MiB")
	all := flag.Bool("all-accounts", false, "enumerate all state accounts or receipts")
	var candidates candidateFlags
	flag.Var(&candidates, "candidate-address-csv", "address discovery CSV; amounts ignored; repeatable for full state")
	flag.Parse()
	require(*dbPath != "" && *manifestPath != "" && *output != "", "db, manifest and output are required")
	require((*accountsPath != "") != *all, "choose accounts or all-accounts")
	require(!*all || *mode != "probe", "all-accounts requires state or receipts")
	require(len(candidates) == 0 || (*all && *mode == "state"), "candidate CSV requires full state")
	require(*shard == 0 || *shard == 1, "shard must be 0 or 1")
	require(*mode == "probe" || *mode == "state" || *mode == "receipts", "invalid mode")
	require(*mode == "probe" || *allowScan, "state/receipts modes require --allow-global-scan")
	require(*probeValidators == "" || (*mode == "probe" && *shard == 0), "probe-validators requires shard-0 probe mode")
	require(*maxAccounts > 0 && *timeout > 0 && *cache > 0, "limits must be positive")
	_, e := os.Stat(*output)
	require(os.IsNotExist(e), "output already exists or cannot be inspected")
	mb, e := os.ReadFile(*manifestPath)
	check(e)
	var m manifest
	check(json.Unmarshal(mb, &m))
	c := m.Cutoff.Shard0
	if *shard == 1 {
		c = m.Cutoff.Shard1
	}
	var ab []byte
	if !*all {
		ab, e = os.ReadFile(*accountsPath)
		check(e)
	}
	accounts := map[string]map[string]string{}
	for _, line := range strings.Split(string(ab), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		addr := hx(unhex(line, 20))
		_, dup := accounts[addr]
		require(!dup, "duplicate account")
		accounts[addr] = map[string]string{}
	}
	require(*all || (len(accounts) > 0 && len(accounts) <= *maxAccounts), "account count exceeds limit or is zero")
	mh := sha256.Sum256(mb)
	r := report{AllAccounts: *all, Schema: "harmony-independent-raw/v1", Mode: *mode, Shard: *shard, Cutoff: c, ManifestSHA: hex.EncodeToString(mh[:]), DB: *dbPath, Accounts: accounts, Counts: map[string]uint64{}}
	ah := sha256.Sum256(ab)
	r.SelectionSHA = hex.EncodeToString(ah[:])
	executable, err := os.Executable()
	check(err)
	executableFile, err := os.Open(executable)
	check(err)
	binaryHash := sha256.New()
	_, err = io.Copy(binaryHash, executableFile)
	executableFile.Close()
	check(err)
	r.ExtractorSHA = hex.EncodeToString(binaryHash.Sum(nil))
	start := time.Now()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, *timeout)
	defer cancel()
	fmt.Fprintf(os.Stderr, "opening read-only database; mode=%s accounts=%d timeout=%s\n", *mode, len(accounts), *timeout)
	db, closeDB := openDB(ctx, *dbPath, *cache)
	defer closeDB()
	verifyCutoff(db, c, *shard)
	if *all && *mode == "state" {
		fullState(db, &r, *output, candidates)
		return
	}
	if *mode == "receipts" {
		scanReceipts(db, &r)
	} else {
		extractState(db, &r, *mode == "state")
		if *probeValidators != "" {
			b, err := os.ReadFile(*probeValidators)
			check(err)
			var validators [][]byte
			seen := map[string]bool{}
			for _, line := range strings.Split(string(b), "\n") {
				line = strings.TrimSpace(line)
				if line == "" {
					continue
				}
				a := unhex(line, 20)
				require(!seen[hx(a)], "duplicate probe validator")
				seen[hx(a)] = true
				validators = append(validators, a)
			}
			require(len(validators) > 0 && len(validators) <= *maxAccounts, "probe validator count exceeds limit or is zero")
			probeStaking(db, &r, validators)
		}
	}
	check(ctx.Err())
	r.Complete = *mode != "probe"
	if !r.Complete {
		r.Limitations = append(r.Limitations, "probe only; staking discovery and cross-shard history NOT complete; never use as a final balance")
	}
	r.Counts["database_reads"] = db.reads
	r.Elapsed = time.Since(start).Seconds()
	saveReport(*output, r)
	fmt.Fprintf(os.Stderr, "saved %s; complete=%t accounts=%d elapsed=%.2fs\n", *output, r.Complete, len(accounts), r.Elapsed)
}
func main() {
	defer func() {
		if p := recover(); p != nil {
			if e, ok := p.(auditError); ok {
				fmt.Fprintln(os.Stderr, "error:", e)
				os.Exit(2)
			}
			panic(p)
		}
	}()
	run()
}
func sortedAddresses(m map[string]map[string]string) []string {
	a := make([]string, 0, len(m))
	for s := range m {
		a = append(a, s)
	}
	sort.Strings(a)
	return a
}
