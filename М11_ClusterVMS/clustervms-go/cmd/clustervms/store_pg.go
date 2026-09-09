//go:build pg

package main

import (
	"context"

	"clustervms/cluster"
)

func openStore(ctx context.Context, dsn string) (cluster.Store, func(), error) {
	s, err := cluster.ConnectPg(ctx, dsn)
	if err != nil {
		return nil, nil, err
	}
	return s, s.Close, nil
}
