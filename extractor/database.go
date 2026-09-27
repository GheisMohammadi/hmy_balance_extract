package main

import (
	"context"
	"fmt"
	"github.com/syndtr/goleveldb/leveldb"
	"github.com/syndtr/goleveldb/leveldb/opt"
	"github.com/syndtr/goleveldb/leveldb/storage"
	"os"
	"path/filepath"
	"strings"
)

// Bucketed checkpoints distribute SSTs by file number modulo 256.
// Delegate locking and metadata reads to LevelDB's read-only filesystem storage.
type bucketStorage struct {
	storage.Storage
	path string
}

func (s bucketStorage) Open(fd storage.FileDesc) (storage.Reader, error) {
	if fd.Type != storage.TypeTable {
		return s.Storage.Open(fd)
	}
	p := filepath.Join(s.path, "tables", fmt.Sprintf("%02x", fd.Num%256), fd.String())
	f, e := os.Open(p)
	if os.IsNotExist(e) {
		return os.Open(strings.TrimSuffix(p, ".ldb") + ".sst")
	}
	return f, e
}
func (s bucketStorage) List(ft storage.FileType) ([]storage.FileDesc, error) {
	// Read-only recovery only requests journals. Never silently claim tables absent.
	if ft&storage.TypeTable != 0 {
		return nil, fmt.Errorf("table enumeration unsupported for read-only bucket storage")
	}
	return s.Storage.List(ft)
}

type database struct {
	*leveldb.DB
	ctx   context.Context
	reads uint64
}

func openDB(ctx context.Context, path string, cache int) (*database, func()) {
	fs, e := storage.OpenFile(path, true)
	check(e)
	var backing storage.Storage = fs
	if st, e := os.Stat(filepath.Join(path, "tables")); e == nil && st.IsDir() {
		backing = bucketStorage{fs, path}
	}
	db, e := leveldb.Open(backing, &opt.Options{ReadOnly: true, ErrorIfMissing: true, BlockCacheCapacity: cache * 1024 * 1024, OpenFilesCacheCapacity: 128, Strict: opt.StrictAll})
	if e != nil {
		fs.Close()
		check(e)
	}
	return &database{DB: db, ctx: ctx}, func() { db.Close(); fs.Close() }
}
func (d *database) get(k []byte) ([]byte, bool) {
	check(d.ctx.Err())
	d.reads++
	b, e := d.DB.Get(k, nil)
	if e == leveldb.ErrNotFound {
		return nil, false
	}
	check(e)
	return b, true
}
