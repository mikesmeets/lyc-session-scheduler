"""add coach role, session coaches, attendance, coach notes

Revision ID: ae8990131c87
Revises: 413731aef875
Create Date: 2026-05-04 21:07:08.219512

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ae8990131c87'
down_revision = '413731aef875'
branch_labels = None
depends_on = None


def _column_exists(conn, table, column):
    insp = sa.inspect(conn)
    return any(c['name'] == column for c in insp.get_columns(table))


def _table_exists(conn, table):
    insp = sa.inspect(conn)
    return table in insp.get_table_names()


def upgrade():
    conn = op.get_bind()

    # ── user.is_coach ─────────────────────────────────────────────────────────
    # Use 'false' not '0' — PostgreSQL rejects integer literals for booleans
    if not _column_exists(conn, 'user', 'is_coach'):
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.add_column(sa.Column('is_coach', sa.Boolean(), nullable=False,
                                          server_default=sa.text('false')))

    # ── session coach notes ───────────────────────────────────────────────────
    if not _column_exists(conn, 'session', 'coach_notes_public'):
        with op.batch_alter_table('session', schema=None) as batch_op:
            batch_op.add_column(sa.Column('coach_notes_public',  sa.Text(), nullable=True))
            batch_op.add_column(sa.Column('coach_notes_private', sa.Text(), nullable=True))

    # ── session_coaches join table ────────────────────────────────────────────
    if not _table_exists(conn, 'session_coaches'):
        op.create_table(
            'session_coaches',
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('session.id'), primary_key=True),
            sa.Column('user_id',    sa.Integer(), sa.ForeignKey('user.id'),    primary_key=True),
        )

    # ── attendance table ──────────────────────────────────────────────────────
    if not _table_exists(conn, 'attendance'):
        op.create_table(
            'attendance',
            sa.Column('id',          sa.Integer(),  primary_key=True),
            sa.Column('session_id',  sa.Integer(),  sa.ForeignKey('session.id'),  nullable=False),
            sa.Column('sailor_id',   sa.Integer(),  sa.ForeignKey('sailor.id'),   nullable=False),
            sa.Column('present',     sa.Boolean(),  nullable=True),
            sa.Column('is_walkin',   sa.Boolean(),  nullable=True,
                                     server_default=sa.text('false')),
            sa.Column('recorded_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('session_id', 'sailor_id', name='unique_attendance'),
        )


def downgrade():
    op.drop_table('attendance')
    op.drop_table('session_coaches')

    with op.batch_alter_table('session', schema=None) as batch_op:
        batch_op.drop_column('coach_notes_private')
        batch_op.drop_column('coach_notes_public')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('is_coach')
